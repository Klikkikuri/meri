"""
WikiPedia Loader

Based on the WikipediaLoader from the langchain_community package, this loader fetches the complete content of the
Wikipedia page, and formats them into sections.

TODO: Make into context aware splitter
"""

from copy import copy, deepcopy
import re
from haystack.components.preprocessors import DocumentSplitter
from haystack import Document

from meri.utils import detect_language

import getpass
import logging
import os
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, TypedDict
from dotenv import load_dotenv

import mwclient
import mwparserfromhell

from trafilatura import load_html
from trafilatura.htmlprocessing import prune_unwanted_nodes
from lxml.etree import XPath
from trafilatura.htmlprocessing import tree_cleaning, convert_tags
from trafilatura.core import Extractor
from trafilatura.xml import xmltotxt

from .scraper import get_user_agent

class WikiDocumentMeta(TypedDict, total=False):
    url: str
    title: str
    summary: str

logger = logging.getLogger(__name__)

SECTIONS_TO_IGNORE: Dict[str, Set] = {
    "en": {
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
    },
    "fi": {
        "Lähteet",
        "Aiheesta muualla",
    }
}


def html_to_markdown(html: str, extractor_args = {}) -> str:
    """
    Special wikipedia-specific HTML to Markdown conversion.

    This function uses trafilatura to convert HTML to Markdown, and then
    performs some additional cleanup to remove unwanted elements and
    formatting.
    """
    from trafilatura.settings import DEFAULT_CONFIG
    config = deepcopy(DEFAULT_CONFIG)

    # Not really needed, but set it to avoid warnings
    config["DEFAULT"].setdefault("USER_AGENTS", get_user_agent())

    _extractor_args = {
        'config': config,
        'output_format': 'markdown',
        'formatting': True,
        'links': True,
        'images': False,
        'tables': True,
        'comments': False
    }
    _extractor_args.update(extractor_args)

    doc = load_html(html)
    if doc is None:
        logger.warning("Failed to load HTML document.")
        raise ValueError("Failed to load HTML document.")

    # Prune unwanted nodes
    doc = prune_unwanted_nodes(doc, [XPath(x) for x in [
        "//script", "//noscript", "//style", "//link", "//meta", "//form", "//input", "//button",
        '//*[contains(@class, "noprint") or contains(@class, "ambox-notice")]',  # Remove "noprint" classed content
    ]])

    # Remove links for pages that don't exist
    doc = prune_unwanted_nodes(doc, [XPath(x) for x in [
        '//*[contains(@class, "mw-redirect") or contains(@class, "new")]',  # Remove "mw-redirect" and "new" classed content
    ]])

    options = Extractor(**_extractor_args)

    doc = tree_cleaning(doc, options)
    doc = convert_tags(doc, options)
    txt = xmltotxt(doc.body, options.formatting)

    # Cleaup hacks
    # Remove citation links
    txt = re.sub(r'\[\s*\d+\]', '', txt)
    # Clean whitespace before word boundaries, left by some inline tags from trafilatura (e.g. <i> <b>)
    txt = re.sub(r'\s+([.,;:!?)])', r'\1', txt)

    return txt


class BetterMarkdownChunker:

    class SectionNode(TypedDict):
        level: int
        title: str
        summary: Optional[str]
        body: Optional[str]
        start: int
        end: int
        children: List["MarkdownChunker.SectionNode"]


    def __init__(self, content: str, language: Optional[str] = None):
        """
        Initialize the MarkdownChunker.

        Args:
            content (str): Markdown document as a single string.
            language (str): Language code for localized ignored sections.
        """
        self.content = content
        self.language = language or detect_language(content)
        self.ignored_sections = SECTIONS_TO_IGNORE.get(language, set())
        self.heading_pattern = re.compile(r"^(#{1,6})\s+(.*)", re.MULTILINE)
        self.doc: "BetterMarkdownChunker.SectionNode"

    def parse(self) -> "BetterMarkdownChunker.SectionNode":
        """
        Parses the Markdown content into hierarchical sections with metadata.
        Filters out ignored or empty sections.

        ..todo:: Implement LRU cache for the parsed document

        Args:
            content (str): The Markdown content to parse.
        """
        content = self.content
        matches = list(self.heading_pattern.finditer(content))

        # Stack to keep track of the parent sections in the current path.
        # Stores the actual SectionData dicts.
        stack = []

        if len(matches) == 0:
            raise ValueError("No headings found in the content.")

        for i, match in enumerate(matches):
            level = len(match.group(1))
            title = match.group(2).strip()

            if title in self.ignored_sections:
                logger.info("Skipping ignored section %r", title)
                continue

            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            body = content[start:end].strip()

            # Create a new section node
            section = {
                "level": level,
                "title": title,
                "summary": "",
                "body": body,
                "start": start,
                "end": end,
                "children": [],
            }

            # Is this the first section?
            if level == 1 and not stack:
                stack.append(section)
                continue

            # Ensure correct parent-child relationship
            while stack and stack[-1]['level'] >= level:
                stack.pop()

            # Add the new section to the current parent
            if stack:
                stack[-1]['children'].append(section)
            stack.append(section)

        return stack[0]  # Return the root node

