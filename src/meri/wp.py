"""
WikiPedia Loader

Based on the WikipediaLoader from the langchain_community package, this loader fetches the complete content of the
Wikipedia page, and formats them into sections.

"""

import logging
import re
from copy import deepcopy
from typing import Dict, List, Optional, Set, TypedDict

import mwclient
import mwclient.page
from haystack import Document
from lxml.etree import XPath  # nosec
from pydantic import AnyHttpUrl
from trafilatura import load_html
from trafilatura.core import Extractor
from trafilatura.htmlprocessing import convert_tags, prune_unwanted_nodes
from trafilatura.xml import xmltotxt

from .scraper import get_user_agent
from .utils import detect_language


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


def _generate_page_query(page: mwclient.page.Page) -> Dict:
    """
    Generate the query parameters for the MediaWiki API to fetch page information.
    """
    args = {}
    match page:
        case mwclient.page.Page(revision=revision):
            args['revids'] = revision
        case mwclient.page.Page(pageid=pageid, ns=0):
            args['pageids'] = pageid
        case mwclient.page.Page(title=title, ns=0):
            args['titles'] = title
        case _:
            raise ValueError("Unsupported page type, expected Page with revision, pageid, or title")
        
    return args


def _fetch_page_url(page: mwclient.page.Page) -> AnyHttpUrl:
    """
    Fetch the canonical URL of the page from the MediaWiki API.
    """

    if 'canonicalurl' not in page._info:
        args = _generate_page_query(page)
        info = page.site.get('query', **args, prop='info', inprop='url')

        # Merge new info into page._info
        info = next(iter(info['query']['pages'].values()))
        if 'canonicalurl' not in info:
            raise ValueError("Failed to fetch page URL - no canonicalurl found for %r", args)

        page._info.update(info)

    return AnyHttpUrl(page._info['canonicalurl'])


def _fetch_page_extract(page: mwclient.page.Page) -> str:
    """
    Fetch the extract of the page from the MediaWiki API.
    """

    if 'extract' not in page._info:
        args = _generate_page_query(page)
        info = page.site.get('query', **args, prop='extracts', explaintext=1, exsentences=3)

        # Merge new info into page._info
        info = next(iter(info['query']['pages'].values()))
        if 'extract' not in info:
            raise ValueError("Failed to fetch page extract - no extract found for %r", args)

        page._info.update(info)

    return str(page._info['extract'])



def page_to_document(page: mwclient.page.Page) -> Document:
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
    html = "\n".join([
        data["parse"]["headhtml"]["*"],
        '<main id="content" class="mw-body"><div id="bodyContent" class="content">',
        f'<h1>{data["parse"]["title"]}</h1>',
        data["parse"]["text"]["*"],
        "</div></main>",
        "</body></html>",
    ])

    # Add the canonical URL to the document
    url = _fetch_page_url(page)
    summary = _fetch_page_extract(page)

    doc = Document(
        id=str(url),  # Use the canonical URL as the document ID
        content=html,
        meta={
            "modified": page.touched,
            "title": page.base_title,
            "language": page.pagelanguage,
            "revision": page.revision,
            "url": url,
            "summary": summary,
        },
    )

    return doc


def mediawiki_html_to_markdown(doc: Document, extractor_args={}) -> Document:
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
            ]
        ],
    )

    # WikiPedia specific cleanup
    # Remove links for pages that don't exist
    html = prune_unwanted_nodes(
        html,
        [
            XPath(x)
            for x in [
                '//a[contains(@class, "mw-redirect") or contains(@class, "new")]',  # Remove "mw-redirect" and "new" classed content
                '//*[contains(@class, "noprint") or contains(@class, "ambox-notice")]',  # Remove "noprint" classed content
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
        self.ignored_sections: Set | List = SECTIONS_TO_IGNORE.get(self.language, set())

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

            logger.debug("Found section %r at level %d", title, level)

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
