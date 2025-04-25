"""
WikiPedia Loader

Based on the WikipediaLoader from the langchain_community package, this loader fetches the complete content of the
Wikipedia page, and formats them into sections.

TODO: Make into context aware splitter
"""

from copy import deepcopy
import re
from haystack import Document
import mwclient.page

from meri.utils import detect_language

import getpass
import logging
import os
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, TypedDict

import mwclient

from trafilatura import load_html
from trafilatura.htmlprocessing import prune_unwanted_nodes
from lxml.etree import XPath  # nosec
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
    },
}


def fetch_revision(page: mwclient.page.Page) -> Document:
    """
    Fetch wikipedia page revision.

    Constructs a html document from the page revision, and converts it to markdown using :func:`html_to_markdown`.
    """

    if not page.exists or not page.revision:
        raise ValueError("Page does not exist or has no revision.")

    params = {
        "oldid": page.revision,
        "format": "json",
        "prop": "text|headhtml",
        "disablelimitreport": True,
        "disableeditsection": True,
        "mobileformat": False,
        "contentmodel": "wikitext",
        "disabletoc": True,
    }

    data = page.site.get("parse", **params)
    if not data or "error" in data:
        raise ValueError("Failed to fetch page revision. Error: %s" % data.get("error", "Unknown error"))
    if "parse" not in data:
        raise ValueError("Failed to fetch page revision - no parse data found.")

    # Construct the HTML document, trafilatura expect document to have a <body>.
    html = "\n".join(
        [
            data["parse"]["headhtml"]["*"],
            '<main id="content" class="mw-body"><div id="bodyContent" class="content">',
            f'<h1>{data["parse"]["title"]}</h1>',
            data["parse"]["text"]["*"],
            "</div></main>",
            "</body></html>",
        ]
    )

    doc = Document(
        content=html,
        meta={
            "title": page.base_title,
            "language": page.pagelanguage,
        },
    )

    return doc


def structured_html_to_markdown(doc: Document, extractor_args={}) -> Document:
    """
    Special Wikipedia-optimized HTML to Markdown conversion.

    This function uses trafilatura to convert HTML to Markdown, and then
    performs some additional cleanup to remove unwanted elements and
    formatting, specific to wikipedia pages.
    """
    from trafilatura.settings import DEFAULT_CONFIG

    config = deepcopy(DEFAULT_CONFIG)

    # Not really needed, but set it to avoid warnings
    config["DEFAULT"].setdefault("USER_AGENTS", get_user_agent())

    _extractor_args = {
        "config": config,
        "output_format": "markdown",
        "formatting": True,
        "links": True,
        "images": False,
        "tables": True,
        "comments": False,
    }
    _extractor_args.update(extractor_args)

    logger.debug("Extractor args: %s", _extractor_args)

    html = load_html(doc.content)
    if html is None:
        raise ValueError("Failed to load HTML document.")

    # Prune unwanted nodes
    html = prune_unwanted_nodes(
        html,
        [
            XPath(x)
            for x in [
                "//script",
                "//noscript",
                "//style",
                "//link",
                "//meta",
                "//form",
                "//input",
                "//button",
                '//*[contains(@class, "noprint") or contains(@class, "ambox-notice")]',  # Remove "noprint" classed content
            ]
        ],
    )

    # # Remove links for pages that don't exist
    html = prune_unwanted_nodes(
        html,
        [
            XPath(x)
            for x in [
                # Remove "mw-redirect" and "new" classed content
                '//a[contains(@class, "mw-redirect") or contains(@class, "new")]',
            ]
        ],
    )

    options = Extractor(**_extractor_args)

    html = convert_tags(html, options)
    # doc = tree_cleaning(doc, options)
    txt = xmltotxt(html.body, options.formatting)

    # Cleaup hacks
    # Remove citation links
    txt = re.sub(r"\[\s*\d+\]", "", txt)
    # Clean whitespace before word boundaries, left by some inline tags from trafilatura (e.g. <i> <b>)
    txt = re.sub(r"\s+([.,;:!?)])", r"\1", txt)

    new_doc = deepcopy(doc)
    new_doc.content = txt

    # Set canonical URL if available
    if canon_url := XPath('//link[@rel="canonical"]/@href')(html):
        new_doc.meta["url"] = canon_url[0]
        new_doc.id = new_doc.meta.get("url", None)

    return new_doc


class SectionNode(TypedDict):
    level: int
    title: str
    summary: Optional[str]
    body: Optional[str]
    start: int
    end: int
    children: List["SectionNode"]


class MarkdownChunker:
    """
    Parses a Markdown document into sections.
    """

    def __init__(self, content: str, language: Optional[str] = None):
        """
        Initialize the MarkdownChunker.

        :param content: The Markdown content to parse.
        :param language: Language code of the content. If not provided, detection is attempted.
        """

        self.content = content
        self.language = language or detect_language(content)
        self.ignored_sections: Set | List = SECTIONS_TO_IGNORE.get(language, set())
        self.heading_pattern = re.compile(r"^(#{1,6})\s+(.*)", re.MULTILINE)

        # Set the root node of the document. Has a side effect that only one main level heading is allowed.
        self.doc: SectionNode

    def parse(self) -> SectionNode:
        """
        Parses the Markdown content into hierarchical sections with metadata.
        Filters out ignored or empty sections.

        ..todo:: Implement LRU cache for the parsed document
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
            while stack and stack[-1]["level"] >= level:
                stack.pop()

            # Add the new section to the current parent
            if stack:
                stack[-1]["children"].append(section)
            stack.append(section)

        return stack[0]  # Return the root node
