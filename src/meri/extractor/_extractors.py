from random import randint
import newspaper
from pydantic import AnyHttpUrl
from opentelemetry import trace
from meri.scraper import get_user_agent
from ._common import RobotsHTTPAdapter

from ._processors import (
    article_url,
    article_canonical_url,
    article_to_markdown,
)

tracer = trace.get_tracer(__name__)


@tracer.start_as_current_span(name="newspaper_extractor", kind=trace.SpanKind.CLIENT)
def newspaper_extractor(url: AnyHttpUrl) -> newspaper.Article:
    """
    Extract the article using the newspaper library.
    """
    span = trace.get_current_span()

    url = str(url)

    span.set_attribute("http.method", "GET")
    span.set_attribute("http.url", url)

    config = newspaper.configuration.Configuration()
    config.fetch_images = False
    config.browser_user_agent = get_user_agent()
    config.request_timeout = randint(5, 12)  # nosec: B311
    span.set_attribute("http.timeout", config.request_timeout)
    span.set_attribute("http.user_agent", config.browser_user_agent)

    # Patch the newspaper library to follow robots.txt
    from newspaper.network import session
    # Check if custom adapter is already set
    if not isinstance(session.adapters["http://"], RobotsHTTPAdapter):
        session.mount("http://", RobotsHTTPAdapter())
        session.mount("https://", RobotsHTTPAdapter())

    article = newspaper.Article(url=url, config=config)

    article.download()
    span.set_attribute("newspaper.Article.download_state", article.download_state)
    span.set_attribute("newspaper.Article.download_exception_msg", str(article.download_exception_msg))

    article.parse()
    span.set_attribute("newspaper.Article.title", article.title)
    span.set_attribute("newspaper.Article.canonical_link", article.canonical_link)

    return article


class NewspaperExtractorMixin:
    """
    Use :class:`newspaper.Article` to extract information from a news article.
    """

    processors: list[callable] = [
        newspaper_extractor,
        article_canonical_url,
        article_url,
        article_to_markdown,
    ]
