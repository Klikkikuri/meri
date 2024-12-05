"""
WikiPedia Loader

Based on the WikipediaLoader from the langchain_community package, this loader fetches the complete content of the
Wikipedia page, and formats them into sections.

TODO: Make into context aware splitter
"""

import re
from haystack.components.preprocessors import DocumentSplitter
from haystack import Document

import getpass
import logging
import os
from typing import Any, Dict, List, Optional, Set, Tuple, TypedDict
from dotenv import load_dotenv

import mwclient
import mwparserfromhell

from .scraper import get_user_agent

class WikiDocumentMeta(TypedDict, total=False):
    url: str
    title: str
    summary: str

logger = logging.getLogger(__name__)

SECTIONS_TO_IGNORE: Dict[str, Set] = {
    "en": set(
        (
            "See also",
            "References",
            "External links",
            "Further reading",
            "Footnotes",
            "Bibliography",
            "Sources",
            "Citations",
            "Literature",
            "Footnotes",
            "Notes and references",
            "Photo gallery",
            "Works cited",
            "Photos",
            "Gallery",
            "Notes",
            "References",
            "References and sources",
            "References and notes",
        )
    ),
    "fi": set(
        (
            "Lähteet",
            "Aiheesta muualla",
        )
    ),
}


def split_markdown_documents(content: str) -> List[str]:
    """
    Document splitting function for Markdown documents.
    """

    # Split the document into sections
    heading_pattern = re.compile(r"^(#+)\s+(.*)", re.MULTILINE)
    matches = list(heading_pattern.finditer(content))

    previous_headings = []
    summary = ""

    # TODO: Make it locale aware
    ignored_sections = SECTIONS_TO_IGNORE["en"] | SECTIONS_TO_IGNORE["fi"]

    docs = []

    # Iterate over headings
    for i, match in enumerate(matches):
        heading_level = len(match.group(1))  # Number of # characters
        heading_text = match.group(2).strip()

        if heading_text in ignored_sections:
            logger.debug("Skipping section skippable section %r", heading_text)
            continue

        # Update the previous headings context
        if heading_level > len(previous_headings):
            previous_headings.append(heading_text)
        else:
            previous_headings = previous_headings[:heading_level - 1]
            previous_headings.append(heading_text)

        # Extract content for this section
        start_pos = match.end()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        section_content = content[start_pos:end_pos].strip()

        if not section_content:  # Skip empty sections
            logger.debug("Skipping empty section %r", heading_text)
            continue

        if heading_level == 1:
            summary = section_content

        page_content = ""

        # Generate headings
        for l, heading in enumerate(previous_headings):
            page_content += f"{'#' * (l + 1)} {heading}\n\n"

            # Include the summary in the first subsection
            if l == 0 and summary and heading_level > 1:
                page_content += f"{summary}\n\n"

        page_content += section_content
        docs.append(page_content)

    return docs


class WikipediaLoader:
    """
    ..todolist::
        - [ ] Implement local caching
    """

    SEE_ALSO_LEVEL = 2
    SEE_ALSO_HEADINGS = {
        "en": "See also",
        "fi": "Katso myös",
    }


    SECTIONS_TO_IGNORE: Dict[str, Set] = {
        "en": set(
            (
                "See also",
                "References",
                "External links",
                "Further reading",
                "Footnotes",
                "Bibliography",
                "Sources",
                "Citations",
                "Literature",
                "Footnotes",
                "Notes and references",
                "Photo gallery",
                "Works cited",
                "Photos",
                "Gallery",
                "Notes",
                "References and sources",
                "References and notes",
            )
        ),
        "fi": set(
            (
                "Lähteet",
                "Aiheesta muualla",
            )
        ),
    }

    site: mwclient.Site

    def __init__(self, lang="en", **kwargs):
        kwargs.setdefault("clients_useragent", get_user_agent())
        self.site = mwclient.Site(f"{lang}.wikipedia.org", **kwargs)

    def search(self, query, limit=10) -> List[mwclient.page.Page]:
        results = self.site.search(query, limit=limit)
        pages = [self.site.pages[p["title"]] for p in results]
        return pages[:limit]

    def search_context(self, query) -> List[mwclient.page.Page]:
        """
        Search for pages and pages they list as related.
        """
        pages = set(self.search(query))

        related_pages = set()
        # Get related pages
        for page in pages:
            related_pages.update(self.get_related_pages(page))

        return list(pages | related_pages)


    def get_related_pages(self, page, lang=None) -> List:
        if lang is None:
            lang = self.site.site['lang']

        SEE_ALSO_TITLE = self.SEE_ALSO_HEADINGS.get(lang, "See also")

        # Find section for "See also"
        parsed_text = mwparserfromhell.parse(page.text())
        related_pages = []
        for section in parsed_text.get_sections(levels=[self.SEE_ALSO_LEVEL]):
            section_heading, *_ = section.filter_headings()
            if section_heading is None: continue
            section_title = str(section_heading.title).strip("=" + " ")

            logger.debug("Checking section %r", section_title)
            if section_title == SEE_ALSO_TITLE:
                logger.debug("Found 'See also' section %r", section_title)
                for link in section.filter_wikilinks():
                    # Check page type, and that it exists)
                    page_title = str(link.title)
                    related_page = self.site.pages[page_title]
                    # namespace 0 is the main namespace for articles
                    if related_page.exists and related_page.namespace == 0:
                        related_pages.append(related_page)
                    else:
                        logger.debug("Ignoring page %r", page_title)

        return related_pages

    def section_page(self, page) -> List[Document]:
        """
        Return a list of tuples, where the first element is the title of the section, and the second is the text.
        """
        wikicode = mwparserfromhell.parse(page.text())
        parent_titles = [page.name]

        lang = self.site.site['lang']
        SECTIONS_TO_IGNORE = self.SECTIONS_TO_IGNORE[lang]

        summary = page.text().split("\n\n")[0]
        doc = Document(
            page_content=f"# {page.name}\n{page.text()}",
            metadata={
                "title": page.name,
            },
        )
        
        for section in wikicode.get_sections(levels=[2]):
            for subsection_title_parts, subsection_text in self.subsections(section, parent_titles):
                # Format the section content
                section_content = ""
                for i, subtitle in enumerate(subsection_title_parts):
                    section_content += f"{'#' * (i + 1)} {subtitle}\n"
                section_content += subsection_text

                yield Document(
                    page_content=section_content.strip(),
                    metadata={
                        "title": " > ".join(subsection_title_parts),
                    },
                )


    def sections(self, page: mwclient.page.Page) -> List[Tuple[str, str]]:
        ...

    def subsections(self, section: mwparserfromhell.wikicode.Wikicode, parent_titles=List[str]) -> List[Tuple[List[str], str]]:
        """
        From a Wikipedia section, return a flattened list of all nested subsections.

        Each subsection is a tuple, where:
        - the first element is a list of parent subtitles, starting with the page title
        - the second element is the text of the subsection (but not any children)
        """

        headings = [str(h) for h in section.filter_headings()]
        if not headings:
            return []
        title = headings[0]
        cleaned_title = title.strip("=" + " ")
        if cleaned_title in SECTIONS_TO_IGNORE:
            logger.debug(f"Ignoring section {cleaned_title}")
            return []

        if len(headings) == 0:
            logger.debug(f"No headings in section {cleaned_title}")
            return []

        titles = parent_titles + [cleaned_title]
        full_text = str(section)
        logger.debug(f"Found section {titles}, splitting body at {title}")
        section_text = full_text.split(title)[1]

        if len(headings) == 1:
            logger.debug(f"Found section {titles}, no subsections")
            # no subsections
            return [(titles, section_text)]

        first_subtitle = headings[1]
        section_text = section_text.split(first_subtitle)[0]
        results = [(titles, section_text)]
        for subsection in section.get_sections(levels=[len(titles) + 1]):
            results.extend(self.subsections(subsection, titles))

        return results

    def pages_to_documents(self, pages: List[mwclient.page.Page]) -> List[Document]:
        ...

    


if __name__ == "__main__":
    from meri.utils import setup_logging

    setup_logging(debug=True)
    logging.basicConfig(level=logging.DEBUG)

    load_dotenv()
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

    # if "OPENAI_API_KEY" not in os.environ:
    #     os.environ['OPENAI_API_KEY'] = keyring.get_password("openai", getpass.getuser())

    wp = WikipediaLoader(lang="fi")

    page = wp.site.pages["Nuorten seksuaalisuus"]
    html = page.html()
    print(html)
    #sections = wp.section_page(page)
    #print(sections)

    #print(wp.search_context("Nuorten seksuaalisuus"))

    #print(search_pages("Nuorten seksuaalisuus", lang="fi"))
    #print(get_related_pages("Nuorten seksuaalisuus", lang="fi"))
