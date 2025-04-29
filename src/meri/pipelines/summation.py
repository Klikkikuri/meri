

import logging
from typing import List, Optional

from haystack import Document, Pipeline
from haystack.components.builders import PromptBuilder

from ..llm import get_generator, get_prompt_template
from ..wp import MarkdownChunker, SectionNode

logger = logging.getLogger(__name__)


class LmmSummationPipeline:

    pipeline: Optional[Pipeline] = None
    """
    Summarization pipeline for the LLM (:class:`~haystack.pipelines.Pipeline`).

    This pipeline is built only once, and cached for later use. See :meth:`_build_pipeline` for details.
    """

    SKIP_TAG: str = "<skip>"
    """
    Tag used to skip sections in the summarization process. Can be used to mark sections that should not be summarized.   
    """

    instructions: str
    """
    Instructions for the LLM to follow when generating the summary. This is used in the prompt template.
    """

    def __init__(self, instructions: str = None):
        """
        Initialize the LmmSummation class.

        :param instructions: Instructions for the LLM to follow when generating the summary.
        :param thread_count: Number of execution threads to use for parallel processing.
        """
        self.instructions = instructions or get_prompt_template("summary_inst")
        self.pipeline = None


    def _build_pipeline(self) -> Pipeline:
        """
        Build the pipeline for summarization.

        This function is called only once, and the pipeline is cached for later use.

        :return: The pipeline object.
        """
        if self.pipeline:
            logger.debug("Pipeline already built, skipping.")
            return self.pipeline

        prompt_builder = PromptBuilder(self.instructions)
        llm = get_generator()

        self.pipeline = Pipeline()
        self.pipeline.add_component("prompt_builder", prompt_builder)
        self.pipeline.add_component("llm", llm)
        self.pipeline.connect("prompt_builder", "llm")

        return self.pipeline


    def _node_to_text(self, node: dict, roots: List[SectionNode]) -> str:
        """
        Generate a text representation of the node and its parent node titles.
        """
        section_titles = [f"{'#' * parent_node['level']} {parent_node['title']}" for parent_node in roots + [node]]
        summaries = [parent_node['summary'] for parent_node in roots] + [node['body']]

        sections = []
        for title, summary in zip(section_titles, summaries):
            match summary:
                case None | "" | self.SKIP_TAG:
                    logger.debug(f"Skipping summary for {title}")
                    sections.append(str(title))
                case _:
                    sections.append(f"{title}\n\n{summary.strip()}".strip())

        return "\n\n".join(sections)

    def build_summaries(self, node: SectionNode, roots: List[SectionNode]):
        """
        Generate summaries for nodes, recursively traversing the tree structure from leaf to root.

        ..note:: This function modifies the node in place, adding a 'summary' key to it.
        """
        branch = roots + [node]
        node_text = self._node_to_text(node, roots)

        # Recurse first (sequentially), collect child summaries later
        if node['children']:
            for child in node['children']:
                self.build_summaries(child, branch)

            sections = [node_text]

            # After all children are scheduled, combine summaries
            subsection_summaries = (child.get("summary") for child in node['children'])
            subsection_titles = (f"{'#' * _node['level']} {_node['title']}" for _node in node['children'])
            for title, summary in zip(subsection_titles, subsection_summaries):
                title = title.strip()
                summary = summary.strip()
                match summary:
                    case None | "" | self.SKIP_TAG:
                        logger.debug(f"Skipping summary for {title} due to empty summary")
                        sections.append(str(title))
                        continue
                    case _:
                        sections.append(f"{title}\n\n<summary>{summary.strip()}</summary>".strip())

            node_text = "\n\n".join(sections)

        # Run the summarization pipeline for the current node
        node['summary'] = self.run_summarize_pipeline(text=node_text, branch=branch)


    def __call__(self, document: Document):
        tree = self.doc_to_tree(document)
        if document.meta['summary'] and document.meta['summary'] != self.SKIP_TAG:
            tree['summary'] = document.meta['summary']

        self.build_summaries(tree, [])

        return tree


    def run_summarize_pipeline(self, text, branch, **kwargs) -> str:
        """
        Summarize the given text using the pipeline.

        :return: The summary of the text.

        :raises RuntimeError: If the LLM fails to generate a summary.
        """
        if not self.pipeline:
            self._build_pipeline()

        assert text, "Text to summarize cannot be empty"

        results = self.pipeline.run({
            "prompt_builder": {
                'SKIP_TAG': self.SKIP_TAG,
                'text': text,
                "article_title": branch[0]['title'],
                "section_title": branch[-1]['title'],
                **kwargs,
            },
        })

        match results:
            case {"llm": {"replies": [summary]}}:
                summary = summary.strip()
                return summary if summary != self.SKIP_TAG else ""
            case {"llm": {"error": error}}:
                raise RuntimeError(f"LLM error: {error}")
            case _:
                logger.error("Unexpected LLM result: %r", results, extra={"kwargs": kwargs, "results": results})
                raise RuntimeError("No summary generated")


    def doc_to_tree(self, doc) -> SectionNode:
        """
        Convert a document to a tree structure.

        :param doc: The document to convert.
        :return: The tree structure of the document.
        """
        tree = MarkdownChunker(doc.content, doc.meta['language'])
        return tree.parse()


    def tree_to_docs(self, base_doc, branches_path):
        docs = []
        ...
