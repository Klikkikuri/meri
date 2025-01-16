from copy import deepcopy
from random import randint
import newspaper
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


@tracer.start_as_current_span(name="newspaper_extractor", kind=trace.SpanKind.CLIENT)
def newspaper_extractor(url: AnyHttpUrl) -> newspaper.Article:
    """
    Extract the article using the newspaper library.
    """
    span = trace.get_current_span()

    url = str(url)


    config = newspaper.configuration.Configuration()
    config.fetch_images = False
    config.browser_user_agent = get_user_agent()
    config.request_timeout = randint(5, 12)  # nosec: B311

    span.set_attribute("http.method", "GET")
    span.set_attribute("http.url", url)
    span.set_attribute("http.timeout", config.request_timeout)
    span.set_attribute("http.user_agent", config.browser_user_agent)

    article = newspaper.Article(url=url, config=config)

    article.download()
    span.set_attribute("newspaper.Article.download_state", article.download_state)
    span.set_attribute("newspaper.Article.download_exception_msg", str(article.download_exception_msg))

    article.parse()
    span.set_attribute("newspaper.Article.title", article.title)
    span.set_attribute("newspaper.Article.canonical_link", article.canonical_link)

    return article


@tracer.start_as_current_span(name="trafilatura_extractor", kind=trace.SpanKind.CLIENT)
def trafilatura_extractor(url: AnyHttpUrl) -> Article:
    """
    Extract the article using the trafilatura library.
    """
    from trafilatura import fetch_url, bare_extraction
    from trafilatura.settings import DEFAULT_CONFIG

    # https://trafilatura.readthedocs.io/en/latest/settings.html
    config = deepcopy(DEFAULT_CONFIG)
    config["DEFAULT"].setdefault("USER_AGENTS", get_user_agent())
    config["DEFAULT"].setdefault("DOWNLOAD_TIMEOUT", randint(5, 12))
    config["DEFAULT"].setdefault("SLEEP_TIME", randint(1, 5))

    url = clean_url(url)

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
    )

    article = Article(
        meta=ArticleMeta(
            title=document['title'],
            language=document['language'] or detect_language(document['text']),
            authors=[] if not document['author'] else [document['author']],
        ),
        text=document['text'],
        urls=[
            article_url(document['url'], type=LinkLabel.LINK_CANONICAL),
            article_url(url, type=LinkLabel.LINK_MOVED),
        ],
    )

    return article


class NewspaperExtractorMixin:
    """
    Use :class:`newspaper.Article` to extract information from a news article.
    """

    processors: list[callable] = [
        check_robots_txt_access,
        newspaper_extractor,
        article_canonical_url,
        extract_article_url,
        article_to_markdown,
    ]


class TrafilaturaExtractorMixin:
    """
    Use :class:`trafilatura.Extractor` to extract information from a news article.
    """

    processors: list[callable] = [
        check_robots_txt_access,
        trafilatura_extractor,
    ]
