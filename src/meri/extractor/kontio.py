"""
Kontio API extractor for fetching full article content.

TODO: The text extracted _might_ contain HTML tags - this is to be verified.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
import pprint
from lxml import etree
from typing import Final, Iterable, Literal, NamedTuple, Optional, TypedDict, Any, Dict, List, Union
from urllib.parse import urlparse

import requests
from pytz import timezone, utc
from structlog import get_logger

from meri.abc import ArticleLabels, ArticleMeta
from meri.article import Article

from ._common import Outlet

logger = get_logger(__name__)

# Kontio API constants
ACCESS_LEVEL_FREE: Final[str] = "free"
ACCESS_LEVEL_PAID: Final[str] = "paid"

"""
        "storyline": [
            {
                "type": "header",
                "data": {
                    "variant": "default",
                    "data": {
                        "paid": false,
                        "access_level": "free",
                        "style": "default",
                        "ingress": null,
                        "headline": "Hamas vahvistaa Israelin surmanneen yhden sen johtajista",
                        "headline_prefix": null,
                        "section": "Uutissuomalainen",
                        "media": {
                            "type": "image",
                            "data": {
                                "type": "external",
                                "data": {
                                    "blur_hash": "UbE30X%2WAxu~qxtRjxb?bt6a#s.-;ozj]jF",
                                    "caption": "Hamasin taistelijoita kuvattuna Gazan kaupungissa tammikuussa 2025. AFP / LEHTIKUVA",
                                    "height": 960,
                                    "semantic_label": null,
                                    "url": "https://i.media.fi/incoming/vub2xp/11201526.jpg/alternates/WEBP_FREE_1440/11201526.webp",
                                    "width": 1440
                                }
                            }
                        },
                        "authors": [
                            {
                                "full_name": "STT –AFP",
                                "avatar_url": null
                            }
                        ],
                        "published_at": "2025-12-14T16:27:16Z",
                        "updated_at": null
                    }
                },
                ...

"""
# Return type for API parameters
class KontioApiParams(NamedTuple):
    """
    API parameters for Kontio article extraction.
    
    Kontio API requires publication, section, and article_id to fetch full article details.

    see: :py:meth:`KontioExtractor.get_api_params` and :py:prop:`KontioExtractor.API_BASE`
    """

    publication: str
    section: str
    article_id: str

class StorylineTextAnnotations(TypedDict, total=False):
    bold: bool
    italic: bool
    uppercase: bool
    impact: bool

class StorylineTextData(TypedDict):
    content: str
    annotations: StorylineTextAnnotations

class StorylineTextBlock(TypedDict):
    type: Literal["text"]
    data: StorylineTextData

class StorylineLinkData(TypedDict):
    href: str
    content: str
    annotations: StorylineTextAnnotations

class StorylineLinkBlock(TypedDict):
    type: Literal["link"]
    data: StorylineLinkData

StorylineRichTextContent = Union[StorylineTextBlock, StorylineLinkBlock]

class StorylineRichTextData(TypedDict):
    content: List[StorylineRichTextContent]

class StorylineRichText(TypedDict):
    type: Literal["rich_text"]
    data: StorylineRichTextData

# --- Header block ---
class StorylineMediaImageDataInner(TypedDict, total=False):
    blur_hash: str
    caption: str
    height: int
    semantic_label: str
    url: str
    width: int

class StorylineMediaImageData(TypedDict):
    type: Literal["external"]
    data: StorylineMediaImageDataInner

class StorylineMedia(TypedDict):
    type: Literal["image"]
    data: StorylineMediaImageData

class StorylineHeaderAuthor(TypedDict):
    full_name: str
    avatar_url: Optional[str]

class StorylineHeaderData(TypedDict):
    paid: bool
    access_level: Literal["free", "paid"]
    style: str
    ingress: Optional[str]
    headline: str
    headline_prefix: Optional[str]
    section: str
    media: Optional[StorylineMedia]
    authors: List[StorylineHeaderAuthor]
    published_at: str
    updated_at: Optional[str]

class StorylineHeaderBlockData(TypedDict):
    variant: Literal["default"]
    data: StorylineHeaderData

class StorylineHeaderBlock(TypedDict):
    type: Literal["header"]
    data: StorylineHeaderBlockData

# --- Ad container block ---
class StorylineAdDataInner(TypedDict, total=False):
    ad_unit_id: str
    prebid_data: Dict[str, Any]

class StorylineAdData(TypedDict):
    type: Literal["ad_data"]
    data: StorylineAdDataInner

class StorylineAdContainerData(TypedDict):
    style: str
    ad: StorylineAdData

class StorylineAdContainerBlock(TypedDict):
    type: Literal["ad_container"]
    data: StorylineAdContainerData

# --- Heading block ---
class StorylineHeadingData(TypedDict):
    level: int
    content: str
    style: Optional[str]
    size: Optional[str]
    sans: bool

class StorylineHeadingBlock(TypedDict):
    type: Literal["heading"]
    data: StorylineHeadingData

# --- Union of all block types ---
StorylineBlock = Union[
    StorylineHeaderBlock,
    StorylineRichText,
    StorylineAdContainerBlock,
    StorylineHeadingBlock,
]


class KontioHTMLTransformer:
    """
    Transform Kontio storyline structures into HTML using lxml.etree.
    """
    def __init__(self):
        """Initialize the HTML transformer."""
        pass


    def _format_dates(self, datetimes: Iterable[str | None]) -> list[etree.Element]:
        """
        Accepts a list of ISO date strings, returns a list of unique <time> elements (lxml.etree.Element).
        Dates are localized to Europe/Helsinki and formatted as '2025-12-14T09:14:13+02:00'.

        TODO: Localize properly - currently Keskisuomalainen is Finnish only.
        """

        eest = timezone('Europe/Helsinki')
        now = datetime.now(utc)
        seen = set()
        time_elements = []

        for dt_str in datetimes:
            print(dt_str)
            if not dt_str or not isinstance(dt_str, str):
                logger.debug("Invalid datetime string in _format_dates", dt_str=dt_str)
                continue
            try:
                dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00')).astimezone(eest)

                if dt.date() == now.date():
                    i18n_str = f"Tänään {dt.strftime("%H:%M")}"
                elif dt.date() == (now - timedelta(days=1)).date():
                    i18n_str = f"Eilen {dt.strftime("%H:%M")}"
                else:
                    i18n_str = dt.strftime('%d.%m.%Y %H:%M')
                if dt in seen:
                    continue
                seen.add(dt)
                time_el = etree.Element('time')
                time_el.set('datetime', dt.isoformat())
                time_el.text = i18n_str
                time_elements.append(time_el)
            except Exception:
                logger.exception("Error parsing datetime in _format_dates", dt_str=dt_str)
                continue
        return time_elements

    def _handle_header(self, data: StorylineHeaderBlockData) -> etree.Element:
        """
        Transform header into separate lead section with headline and ingress.
        """
        match data.get("variant"):
            case "default":
                return self._handle_header_default(data.get("data", {}))
            case other:
                logger.warning("Unknown header variant in _handle_header", variant=other)
                return self._handle_header_default(data.get("data", {}))

    def _handle_header_default(self, data: StorylineHeaderData) -> etree.Element:
        header = etree.Element("header", {"class": "article-header"})
        
        if headline := data.get("headline"):
            h1 = etree.SubElement(header, "h1", {"class": "headline"})
            h1.text = headline
        
        # Add authors if available
        if authors := data.get("authors"):
            if isinstance(authors, list) and authors:
                byline = etree.SubElement(header, "p", {"class": "byline"})
                author_names = []
                for author in authors:
                    if isinstance(author, dict) and (name := author.get("full_name")):
                        author_names.append(name)

                if author_names:
                    byline.text = f"By {', '.join(author_names)}"

        # Add publication dates
        published_at = data.get("published_at")
        updated_at = data.get("updated_at")
        if dates := self._format_dates([published_at, updated_at]):
            time_elem = etree.SubElement(header, "div", {"class": "diks-date "})
            
            pub = dates[0]
            pub.set("class", "published-at")
            time_elem.append(pub)
            if len(dates) > 1:
                # TODO: Localize
                pub.tail = " | Päivitetty "
                upd = dates[1]
                upd.set("class", "updated-at")
                time_elem.append(upd)

        # Add main image
        if media := data.get("media"):
            if img_elem := self._handle_media(media):
                header.append(img_elem)

        # Add ingress
        if ingress := data.get("ingress"):
            p = etree.SubElement(header, "p", {"class": "ingress"})
            p.text = ingress
        
        return header

    
    def _handle_media(self, media: StorylineMedia) -> Optional[etree.Element]:
        """Dispatch media handling based on type."""
        match media:
            case {"type": "image", "data": data}:
                return self._handle_image(data)
            case other:
                logger.warning("Unknown media type in _handle_media", media_type=other)
                return None

    def _handle_image(self, image_data: StorylineMediaImageData) -> Optional[etree.Element]:
        """Transform media data into HTML image."""

        if not isinstance(image_data, dict):
            logger.warning("Invalid image data structure in _handle_image", image_data_type=type(image_data))
            return None
            
        if image_data.get("type") != "external":
            logger.warning("Unknown image data type in _handle_image", image_data_type=image_data.get("type"), image_data_keys=list(image_data.keys()) if image_data else None)
            return None
            
        external_data = image_data.get("data", {})
        if not isinstance(external_data, dict):
            logger.warning("Invalid external data structure in _handle_image", external_data_type=type(external_data))
            return None
            
        url = external_data.get("url")
        caption = external_data.get("caption")
        
        if not url:
            logger.warning("Missing URL in external image data in _handle_image")
            return None
            
        figure = etree.Element("figure")
        
        etree.SubElement(figure, "img", {
            "src": url,
            "alt": caption if caption else ""
        })
        
        # Add caption if present
        if caption:
            figcaption = etree.SubElement(figure, "figcaption")
            figcaption.text = caption
        
        return figure


    def _handle_heading(self, heading_data: StorylineHeadingData) -> Optional[etree.Element]:
        """Transform heading block into HTML heading."""

        level = heading_data.get("level", 2)
        content = heading_data.get("content", "")
        
        if not content:
            return None

        # Ensure level is between 1-6
        level = max(1, min(6, level))

        heading = etree.Element(f"h{level}")
        heading.text = content

        return heading

    def _handle_rich_text(self, rich_data: dict) -> Optional[etree.Element]:
        """Transform rich text block into HTML paragraph."""
        if not isinstance(rich_data, dict):
            return None
            
        content = rich_data.get("content", [])
        if not isinstance(content, list):
            return None
            
        # Create paragraph element
        p = etree.Element("p")
        p.text = ""

        for item in content:
            
            item_type = item.get("type")
            item_data = item.get("data", {})

            text = item_data.get("content", "")
            if item_data.get("annotations", {}).get("uppercase", False):
                text = text.upper()

            match item_type, item_data:
                case ("text", _):
                    # Add text directly to paragraph
                    p.text += text
                case ("link", {"href": href}):
                    if href:
                        # Create link element
                        a = etree.SubElement(p, "a", {"href": href})
                        a.text = text
                case _:
                    # Log warning for unknown item types
                    logger.warning("Unknown rich text item type encountered", item_type=item_type, item_data_keys=list(item_data.keys()) if item_data else None)

            if item_data.get("annotations", {}).get("bold"):
                logger.warning("Bold annotation handling not implemented yet")
            if item_data.get("annotations", {}).get("italic"):
                logger.warning("Italic annotation handling not implemented yet")
            if item_data.get("annotations", {}).get("impact"):
                logger.warning("Impact annotation handling not implemented yet")
            
        return p

    def _skip(self, part: dict) -> None:
        """Null handler that returns an HTML comment indicating skipped part."""
        return None


    def _handle_block_quote(self, quote_data: dict) -> Optional[etree.Element]:
        """Transform quote block into HTML blockquote."""
        if not isinstance(quote_data, dict):
            return None
            
        quote_text = quote_data.get("quote", "")
        attribution = quote_data.get("attribution", "")
        
        if not quote_text:
            return None
            
        blockquote = etree.Element("blockquote")
        
        # Add quote text
        p = etree.SubElement(blockquote, "p")
        p.text = quote_text
        
        # Add attribution if present
        if attribution:
            cite = etree.SubElement(blockquote, "cite")
            cite.text = f"— {attribution}"
        
        return blockquote

    def transform_storyline(self, storyline: Optional[list]) -> str:
        """
        Transform a complete Kontio storyline (list of parts) into a safe HTML string.
        """
        if not storyline:
            return ""

        # Create a root div to contain the article body content
        body = etree.Element("article")

        # Dispatch table for different storyline part types
        handler_map = {
            "header": self._handle_header,
            "heading": self._handle_heading,
            "rich_text": self._handle_rich_text,
            "ad_container": self._skip,  # Skip ad containers
            "image": self._handle_image,
            "block_quote": self._handle_block_quote,
        }

        for part in storyline:
            part_type = part.get("type")
            handler = handler_map.get(part_type)
            
            if handler:
                try:
                    el = handler(part.get("data"))
                    if el is not None:
                        body.append(el)
                    else:
                        # HACK: Skip logging for known skip handlers
                        if handler.__name__ != "_skip":
                            logger.warning(
                                "Handler returned None unexpectedly",
                                part_type=part_type,
                                handler=handler.__name__,
                            )
                except Exception as e:
                    # Log error gracefully and insert an HTML comment placeholder
                    logger.exception("Error processing storyline part", part_type=part_type, error=str(e))
                    comment = etree.Comment(f"Error processing {part_type}: {e}")
                    body.append(comment)
            else:
                # Log unhandled types
                logger.warning("Unhandled storyline part type", part_type=part_type)
                comment = etree.Comment(f"Skipping unhandled part type: {part_type}")
                body.append(comment)

        # Convert the lxml Element tree to a HTML string. 
        # `pretty_print=True` for readability. `encoding='unicode'` for Python string output.
        return etree.tostring(body, pretty_print=True, encoding='unicode')


class KontioExtractor(Outlet, ABC):
    """
    Extract full article content from Kontio API.

    This is a base class for Kontio-based news outlets, part of Keskisuomalainen media group.
    Subclasses must implement :meth:`get_api_params` to provide publication-specific configuration.
    """

    # API endpoint template for fetching article details
    API_BASE = "https://api.prod.kontio.diks.fi/api/v1/publications/{publication}/sections/{section}/stories/{article_id}"
    "API endpoint template for fetching article details. Fields filled from :py:class:`KontioApiParams`."

    # Block types in storyline
    BLOCK_TYPE_HEADER: Final[str] = "header"
    BLOCK_TYPE_RICH_TEXT: Final[str] = "rich_text"
    BLOCK_TYPE_QUOTE: Final[str] = "quote"
    BLOCK_TYPE_TEXT: Final[str] = "text"

    @abstractmethod
    def get_api_params(self, article: Article) -> KontioApiParams:
        """Extract API parameters from article.

        Subclasses must implement this to provide publication-specific logic
        for extracting publication, section, and article_id.

        :param article: Article stub from discoverer
        :returns: KontioApiParams with publication, section, and article_id
        """
        pass

    def fetch_by_article(self, article: Article) -> Article:
        """
        Fetch full article content using the Kontio API.

        :param article: Article stub from discoverer containing metadata
        :returns: Article with full text content extracted from API
        """
        logger.debug("Fetching full article content via Kontio API", article_id=article.meta.get("id"))

        # Get API parameters from subclass or generic implementation
        params = self.get_api_params(article)

        # Build API URL
        api_url = self.API_BASE.format(**params._asdict())

        logger.debug("Fetching from Kontio API", api_url=api_url)

        # Fetch article data from API
        response = requests.get(api_url, timeout=15)
        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            logger.error("Kontio API returned ok=false", api_url=api_url)
            raise ValueError("Kontio API request failed")

        # Extract article content
        full_article = self._parse_article_data(data.get("data", {}), article)

        return full_article

    def _parse_article_data(self, data: dict, original_article: Article) -> Article:
        """Parse full article data from API response.

        :param data: API response data dictionary
        :param original_article: Original article stub from discoverer
        :returns: Article with full content and updated metadata
        """
        meta_data = data.get("meta", {})
        storyline = data.get("storyline", [])

        # Extract text content from storyline
        text_content = self._extract_text_from_storyline(storyline)

        if not text_content:
            logger.warning("No text content found in storyline", article_id=meta_data.get("id"))
            # Return original article if we can't extract content
            return original_article

        # Build updated metadata
        meta = ArticleMeta(original_article.meta)

        # Update with additional metadata from full article if available
        if headline := meta_data.get("headline"):
            meta["title"] = headline

        # Parse timestamps with timezone awareness
        created_at = original_article.created_at
        updated_at = original_article.updated_at

        if published_at := meta_data.get("published_at"):
            created_at = datetime.fromisoformat(published_at).astimezone(utc)

        if updated_at_str := meta_data.get("updated_at"):
            updated_at = datetime.fromisoformat(updated_at_str).astimezone(utc)
        elif not updated_at:
            updated_at = created_at

        # Check access level for paywall
        labels = list(original_article.labels)
        access_level = meta_data.get("access_level", ACCESS_LEVEL_FREE)
        if access_level != ACCESS_LEVEL_FREE and ArticleLabels.PAYWALLED not in labels:
            labels.append(ArticleLabels.PAYWALLED)
            logger.debug("Article has restricted access", access_level=access_level)

        # Check for sponsored content
        if advertiser := meta_data.get("advertiser"):
            if ArticleLabels.SPONSORED not in labels:
                labels.append(ArticleLabels.SPONSORED)
                logger.debug("Article is sponsored", advertiser=advertiser)

        # Build full article
        full_article = Article(
            text=text_content,
            meta=meta,
            labels=labels,
            urls=original_article.urls,
            created_at=created_at,
            updated_at=updated_at,
        )

        return full_article

    def _extract_text_from_storyline(self, storyline: list[dict]) -> str:
        """
        Extract text content from storyline blocks.

        The storyline contains various block types. We focus on 'rich_text' blocks
        which contain the actual article text content.

        :param storyline: List of storyline block dictionaries from API
        :returns: Extracted text content joined with double newlines
        """
        text_blocks: list[str] = []

        for block in storyline:
            block_type = block.get("type")

            match block_type:
                case "header":
                    # Extract headline/ingress from header
                    header_data = block.get("data", {}).get("data", {})
                    if ingress := header_data.get("ingress"):
                        text_blocks.append(ingress)

                case "rich_text":
                    # Extract text from rich_text content blocks
                    content_items = block.get("data", {}).get("content", [])
                    for item in content_items:
                        if item.get("type") == self.BLOCK_TYPE_TEXT:
                            if text := item.get("data", {}).get("content"):
                                text_blocks.append(text)

                case "quote":
                    # Extract quote text
                    quote_data = block.get("data", {})
                    if quote_text := quote_data.get("quote"):
                        text_blocks.append(f'"{quote_text}"')
                    if attribution := quote_data.get("attribution"):
                        text_blocks.append(f"— {attribution}")

                case _:
                    # Skip ad_container, story_list_tail_container, and other non-content blocks
                    pass

        # Join all text blocks with double newlines for paragraph separation
        return "\n\n".join(text_blocks)


class KSMLExtractor(KontioExtractor):
    """Extractor for Keskisuomalainen (KSML) articles via Kontio API."""

    name = "KSML"
    valid_url = r"https://www\.ksml\.fi/"
    weight = 60
    
    # KSML API publication identifier
    PUBLICATION_ID = "ksml"

    def get_api_params(self, article: Article) -> KontioApiParams:
        """Extract API parameters for KSML articles.

        :param article: Article stub from discoverer
        :returns: KontioApiParams with publication, section, and article_id
        """
        article_id = article.meta.get("id")
        if not article_id:
            raise ValueError("Article missing ID for KSML API extraction")

        url = article.get_url()
        if not url:
            raise ValueError("Article missing URL")

        # KSML URL format: https://www.ksml.fi/{section}/{id}
        parsed_url = urlparse(str(url))
        path_parts = parsed_url.path.rstrip('/').split('/')
        if len(path_parts) < 2:
            raise ValueError(f"Cannot parse KSML article URL: {url}")

        section = path_parts[-2]
        
        # KSML always uses "ksml" as publication identifier
        return KontioApiParams(
            publication=self.PUBLICATION_ID,
            section=section,
            article_id=str(article_id),
        )


if __name__ == "__main__":
    # from pydantic import HttpUrl
    # from ..discovery.kontio import KontioDiscoverer

    # # Test with discovery + extraction using KSML-specific extractor
    # discoverer = KontioDiscoverer()
    # articles = discoverer.discover(
    #     HttpUrl("https://api.prod.kontio.diks.fi/api/v1/publications/ksml/feeds/paajutut?page=1")
    # )

    # if articles:
    #     extractor = KSMLExtractor()
    #     first_article = articles[0]
    #     print(f"\n=== Extracting: {first_article.meta.get('title', 'Unknown')} ===")
    #     print("URL:", first_article.get_url())

    #     try:
    #         full_article = extractor.fetch(first_article)
    #         pprint.pprint(full_article.model_dump())
    #     except Exception as e:
    #         print(f"Error extracting article: {e}")
    #         import traceback
    #         traceback.print_exc()

    import json
    from src.meri.extractor.kontio import KontioHTMLTransformer

    # Load the data
    with open('docs/ksml/9075262.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    storyline = data['data']['storyline']

    # Create transformer and generate HTML
    transformer = KontioHTMLTransformer()
    html_output = transformer.transform_storyline(storyline)
    print(html_output)
