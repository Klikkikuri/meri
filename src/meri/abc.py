from abc import ABC
from dataclasses import dataclass
from opentelemetry import trace
from opentelemetry.semconv.trace import SpanAttributes
from typing import Any, Optional
from re import Pattern
from urllib.parse import ParseResult
import newspaper
from pydantic import AnyHttpUrl

tracer = trace.get_tracer(__name__)

type UrlPattern = Pattern | AnyHttpUrl | ParseResult

class Outlet(ABC):
    name: Optional[str] = None
    valid_url = Pattern | list[Pattern]

    weight: Optional[int] = 50
    # TODO: Add frequency
    frequency: Any

    processors: list[callable]

    def __init__(self) -> None:
        self.processors = []

    def __getattr__(self, name: str) -> Optional[str]:
        if name == "name":
            return self.__class__.__name__
        elif name == "weight":
            return 50

    def latest(self) -> list[newspaper.Article]:
        raise NotImplementedError


    def fetch(self, url: str) -> newspaper.Article:
        raise NotImplementedError


class NewspaperExtractorMixin:
    """
    Use :class:`newspaper.Article` to extract information from a news article.
    """

    def __init__(self) -> None:
        if not self.processors:
            self.processors = []

        self.processors += [
            _article_canonical_url,
            _article_url
        ]

    def fetch(self, url: str) -> newspaper.Article:
        with tracer.start_as_current_span("child") as span:
            span.set_attribute(SpanAttributes.HTTP_METHOD, "GET")
            span.set_attribute(SpanAttributes.HTTP_URL, url)

            article = newspaper.Article(url=url)
            article.download()
            article.parse()

            span.set_attribute("article.title", article.title)
            span.set_attribute("article.canonical_link", article.canonical_link)

        return article

def _article_canonical_url(article: newspaper.Article) -> AnyHttpUrl | None:
    """
    Get the canonical URL of the article.
    """
    url = article.canonical_link
    if url and url != article.original_url:
        return AnyHttpUrl(url)
    return None

def _article_url(article: newspaper.Article) -> AnyHttpUrl | None:
    """
    Get the URL of the article.
    """
    if article.url != article.original_url:
        return AnyHttpUrl(article.url)
    return None
