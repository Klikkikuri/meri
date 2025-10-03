from copy import deepcopy
import datetime
from random import randint
from pydantic import AnyHttpUrl
from opentelemetry import trace

from meri.utils import clean_url, detect_language
from meri.abc import Article, ArticleMeta, LinkLabel, article_url
from meri.scraper import get_user_agent


from ._processors import (
    extract_article_url,
    article_canonical_url,
    article_to_markdown,
    check_robots_txt_access,
)


tracer = trace.get_tracer(__name__)


@tracer.start_as_current_span(name="trafilatura_extractor", kind=trace.SpanKind.CLIENT)
def trafilatura_extractor(url: AnyHttpUrl | str) -> Article:
    """
    Extract the article using the trafilatura library.
    """
    from trafilatura import fetch_url, bare_extraction
    from trafilatura.settings import DEFAULT_CONFIG

    # Find date in ISO 8601 format
    date_iso_format = r"%Y-%m-%dT%H:%M:%S%z"

    # https://trafilatura.readthedocs.io/en/latest/settings.html
    config = deepcopy(DEFAULT_CONFIG)
    config["DEFAULT"].setdefault("USER_AGENTS", get_user_agent())
    config["DEFAULT"].setdefault("DOWNLOAD_TIMEOUT", str(randint(5, 12)))  # nosec
    config["DEFAULT"].setdefault("SLEEP_TIME", str(randint(1, 5)))  # nosec

    url = clean_url(str(url))

    downloaded = fetch_url(url, config=config)

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
        date_extraction_params={"outputformat": date_iso_format, "deferred_url_extractor": True, "max_date": None },
    )

    # # TODO: find_date is bad at finding "created" dates, it often finds "modified" dates instead
    # date_published = find_date(downloaded, url=url, outputformat=iso_format, original_date=True, deferred_url_extractor=True)
    # date_modified = find_date(downloaded, url=url, outputformat=iso_format, original_date=False, deferred_url_extractor=True)

    article = Article(
        meta=ArticleMeta(
            title=document.title,
            language=document.language or detect_language(document.text),
            authors=[] if not document.author else [document.author],
            date=document.date,
        ),
        text=document.text,
        html=downloaded,
        urls=[
            article_url(document.url, labels=[LinkLabel.LINK_CANONICAL], created_at=document.date),
        ],

        created_at=document.date or datetime.datetime.now(datetime.timezone.utc),
        updated_at=document.date or datetime.datetime.now(datetime.timezone.utc)
    )

    if document.url != url:
        article.urls.append(article_url(url, type=LinkLabel.LINK_MOVED))

    return article


class TrafilaturaExtractorMixin:
    """
    Use :class:`trafilatura.Extractor` to extract information from a news article.
    """

    processors: list[callable] = [
        check_robots_txt_access,
        trafilatura_extractor,
    ]
