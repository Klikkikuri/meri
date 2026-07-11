import logging
from typing import Any, ClassVar, Optional

from haystack import Pipeline
from haystack.components.builders import ChatPromptBuilder
from haystack.dataclasses import ChatMessage
from pydantic import BaseModel

from meri.settings import settings

from ..llm import PipelineType, get_generator

logger = logging.getLogger(__name__)


class StructuredPipeline:
    """
    Common class for pipelines utilizing pydantic models as output.
    """

    pipeline: Optional[Pipeline]
    output_model: BaseModel

    PIPELINE_NAME: ClassVar = PipelineType.DEFAULT
    
    prompt_templates: dict[str, str] = {}

    _prompt: ChatPromptBuilder
    _llm: Any

    def __init__(self):
        """
        Initialize the StructuredPipeline class.
        """
        self.pipeline = None


    def _build_pipeline(self) -> Pipeline:
        """
        Build the pipeline for title generation.
        This function is called only once, and the pipeline is cached for later use.
        :return: The pipeline object.
        """
        if self.pipeline:
            logger.debug("Pipeline already built, skipping.")
            return self.pipeline

        prompt_template = "\n\n".join(self.prompt_templates.values())

        self._prompt = ChatPromptBuilder([
            ChatMessage.from_system(prompt_template),
            ChatMessage.from_user("Now, please generate the response."),
        ])

        # Request native structured output from the model by passing output_model to response_format
        self._llm = get_generator(self.PIPELINE_NAME, settings, response_format=self.output_model)

        self.pipeline = Pipeline()
        self.pipeline.add_component("prompt_builder", self._prompt)
        self.pipeline.add_component("llm", self._llm)

        self.pipeline.connect("prompt_builder", "llm")

        return self.pipeline
    
    def run(self, prompt_vars, **kwargs) -> BaseModel:
        pipeline = self._build_pipeline()

        prompt_vars = {**prompt_vars, **kwargs}
        prompt_vars.setdefault("settings", settings)

        # HACK: Haystack prompt -class bitches if it receives extra variables
        prompt_vars = {k: v for k, v in prompt_vars.items() if k in self._prompt.variables}

        if settings.DEBUG:
            print(self._prompt.run(template_variables=prompt_vars)['prompt'][0].text)

        results = pipeline.run({
            "prompt_builder": prompt_vars,
        })
        match results:
            case {"llm": {"replies": [reply, *rest]}}:
                content = reply.text
                if not content:
                    raise ValueError("Empty response from LLM")
                
                # Parse the response using the output_model
                model_output = self.output_model.model_validate_json(content)
                return model_output
            case _:
                logger.error("Invalid pipeline output", extra={"pipeline": pipeline, "results": results})
                raise ValueError(f"Invalid pipeline output: {results!r}")
