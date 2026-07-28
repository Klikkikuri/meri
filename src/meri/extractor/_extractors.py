from copy import deepcopy
from datetime import datetime, timezone
from random import randint
from typing import cast
from zoneinfo import ZoneInfo

from opentelemetry.trace import SpanKind
from pydantic import AnyHttpUrl
from niitti import get_logger
from niitti.tracing import span

from meri.abc import ArticleMeta, LinkLabel, article_url
from meri.scraper import get_user_agent
from meri.utils import clean_url, detect_language

from ._common import HtmlArticle
from ._cfchallenge import CloudflareStatus, cloudflare_status

logger = get_logger(__name__)

# Default timezone to assume for naive dates extracted from articles. This is used when the extracted date does not
# include timezone information.
DEFAULT_TIMEZONE = ZoneInfo("Europe/Helsinki")


class TrafilaturaArticle(HtmlArticle):
    """
    An article extracted using the trafilatura library.
    """

    pass


@span("trafilatura_extractor", kind=SpanKind.CLIENT)
def trafilatura_extractor(url: AnyHttpUrl | str) -> TrafilaturaArticle:
    """
    Extract the article using the trafilatura library.
    """
    from trafilatura import bare_extraction, fetch_url
    from trafilatura.settings import DEFAULT_CONFIG
    from trafilatura.settings import Document

    # Find date in ISO 8601 format
    date_iso_format = r"%Y-%m-%dT%H:%M:%S%z"

    # https://trafilatura.readthedocs.io/en/latest/settings.html
    config = deepcopy(DEFAULT_CONFIG)
    config["DEFAULT"]["USER_AGENTS"] = get_user_agent()
    config["DEFAULT"]["MAX_REDIRECTS"] = "3"  # Trafilature uses this also for retries
    config["DEFAULT"].setdefault("DOWNLOAD_TIMEOUT", str(randint(5, 12)))  # nosec
    config["DEFAULT"].setdefault("SLEEP_TIME", str(randint(1, 5)))  # nosec

    from opentelemetry.trace import get_current_span

    url = clean_url(str(url))

    span = get_current_span()
    span.set_attribute("url", url)

    downloaded = fetch_url(url, config=config)

    if not downloaded:
        logger.warning("Trafilatura failed to download URL: %s", url)
        raise ValueError(f"Failed to download URL {url}")

    document = bare_extraction(
        downloaded,
        url=url,
        include_formatting=True,
        include_comments=False,
        include_images=True,
        with_metadata=True,
        include_tables=True,
        include_links=True,
        config=config,
        date_extraction_params={
            "outputformat": date_iso_format,
            "deferred_url_extractor": True,
            "max_date": None,
            "url": url,
        },
    )
    document = cast(Document | None, document)

    if not document:
        logger.warning("Trafilatura failed to extract article from URL: %s", url, extra={"downloaded": downloaded})
        raise ValueError(f"Failed to extract article from URL {url}")

    # Check for cloudflare block
    match cloudflare_status(downloaded):
        case CloudflareStatus.CHALLENGE:
            logger.warning("Cloudflare __challenge__ detected for URL: %s", url, extra={"document": document})
            raise ValueError(f"Cloudflare challenge detected for URL {url}")
        case CloudflareStatus.BLOCKED:
            logger.error("Cloudflare __block__ detected for URL: %s", url, extra={"document": document})
            raise ValueError(f"Cloudflare block detected for URL {url}")
        case CloudflareStatus.OK | True:
            pass  # No challenge or block detected

    # # TODO: find_date is bad at finding "created" dates, it often finds "modified" dates instead
    # date_published = find_date(downloaded, url=url, outputformat=iso_format, original_date=True, deferred_url_extractor=True)
    # date_modified = find_date(downloaded, url=url, outputformat=iso_format, original_date=False, deferred_url_extractor=True)

    if not document.text:
        logger.warning("Trafilatura extracted article has no text URL: %r", url, extra={"document": document})
        raise ValueError(f"Extracted article has no text (url: {url!r})")

    article = TrafilaturaArticle(
        meta=ArticleMeta(
            title=document.title,
            language=document.language or detect_language(document.text),
            authors=[] if not document.author else [document.author],
        ),
        text=document.text,
        urls=[
            article_url(
                document.url,  # type: ignore[arg-type]
                labels=[LinkLabel.LINK_CANONICAL]
            ),
        ],
        created_at=None,
        updated_at=None,

        html=downloaded  # type: ignore[arg-type]
    )


    if document and document.date:
        parsed_date = datetime.fromisoformat(document.date)
        if parsed_date.tzinfo is None:
            logger.warning(
                "Extracted date is not timezone-aware; assuming default timezone.",
                extracted_date=document.date,
                default_timezone=str(DEFAULT_TIMEZONE),
            )
            parsed_date = parsed_date.replace(tzinfo=DEFAULT_TIMEZONE)

        parsed_date = parsed_date.astimezone(timezone.utc)

        article.updated_at = parsed_date

    else:
        article.created_at = datetime.now(timezone.utc)

    if document.url != url:
        article.urls.append(article_url(url, type=LinkLabel.LINK_MOVED))

    return article


class TrafilaturaExtractorMixin:
    """
    Use :class:`trafilatura.Extractor` to extract information from a news article.
    """

    def fetch_by_url(self, url: AnyHttpUrl | str) -> TrafilaturaArticle:
        """
        Fetch the article from the URL using :class:`trafilatura.Extractor`.
        """
        url = clean_url(str(url))

        article = trafilatura_extractor(url)
        if not article or not article.text:
            raise ValueError(f"Failed to extract article from URL {url}")

        from ._processors import label_paywalled_content
        article = label_paywalled_content(article)

        return article
