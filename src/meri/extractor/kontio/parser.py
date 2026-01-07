from datetime import datetime, timedelta
from typing import Iterable, Optional

from lxml import etree
from pytz import utc, timezone
from structlog import get_logger

from meri.extractor._processors import html_to_markdown

from .types import (
    StorylineHeaderBlockData,
    StorylineHeaderData,
    StorylineHeadingData,
    StorylineMedia,
    StorylineMediaImageData,
    StorylineRichTextData,
)

logger = get_logger(__name__)

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

        eest = timezone("Europe/Helsinki")
        now = datetime.now(utc)
        seen = set()
        time_elements = []

        for dt_str in datetimes:
            if dt_str is None:
                continue
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
                logger.warning("Unknown header variant in _handle_header", extra={"variant": other})
                return self._handle_header_default(data.get("data", {}))

    def _handle_header_default(self, data: StorylineHeaderData) -> etree.Element:

        header = etree.Element("div", {"class": "diks-article-top "})
        
        if headline := data.get("headline"):
            # <h1 class="diks-article__headline"><span class="diks-ui-accent">Janne Yläjoen näkökulma | </span>
            h1 = etree.SubElement(header, "h1", {"class": "diks-article__headline"})

            h1.text = ""
            if headline_prefix := data.get("headline_prefix"):
                span = etree.SubElement(h1, "span", {"class": "diks-ui-accent"})
                span.text = headline_prefix + " | "
                

            h1.text += headline

        byline = etree.SubElement(header, "div", {"class": "diks-byline "})

        # Add authors if available
        if authors := data.get("authors"):
            if isinstance(authors, list) and authors:
                author_elem = etree.SubElement(byline, "p", {"class": "diks-byline__author"})
                author_names = []
                for author in authors:
                    if isinstance(author, dict) and (name := author.get("full_name")):
                        author_names.append(name)

                if author_names:
                    author_elem.text = f"{', '.join(author_names)}"

        # Add publication dates
        published_at = data.get("published_at")
        updated_at = data.get("updated_at")
        if dates := self._format_dates([published_at, updated_at]):
            time_elem = etree.SubElement(byline, "div", {"class": "diks-date "})

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

    def _handle_rich_text(self, rich_data: StorylineRichTextData) -> Optional[etree.Element]:
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


class KontioMdTransformer(KontioHTMLTransformer):
    """
    Transform Kontio storyline structures into Markdown format.
    """

    def transform_storyline(self, storyline: Optional[list]) -> str:
        html = super().transform_storyline(storyline)
        markdown = html_to_markdown(html)
        return markdown
