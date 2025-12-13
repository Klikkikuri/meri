from abc import ABC
from datetime import datetime, timedelta
from re import Pattern
from typing import Callable, Generic, Iterable, Optional, Protocol, TypeVar

from pydantic import AnyHttpUrl, Field, HttpUrl
from structlog import get_logger

from meri.article import Article

logger = get_logger(__name__)

T = TypeVar('T', bound=Article)
""" Type variable for generic types. """

class OutletProtocol(Protocol, Generic[T]):
    name: str

    def fetch(self, source) -> T: ...
    def latest(self) -> list[T]: ...


class HtmlArticle(Article):
    """
    An article containing raw HTML content.
    """

    html: str = Field(..., description="Raw HTML of the article as a string.")


# TODO: Make as pydantic model
class Outlet(ABC):
    name: Optional[str] = None
    valid_url: Pattern | list[Pattern] | str

    weight: Optional[int] = 50

    processors: list[Callable] = []

    def __init__(self) -> None:
        self.processors = []
        # Get classes this instance is a subclass of, and add their processors
        logger.debug("Adding processors from %s", self.__class__.__name__, extra={"base_classes": self.__class__.__mro__})
        for cls in self.__class__.__mro__:
            logger.debug("Checking class %s", cls.__name__)
            if cls in [Outlet, object]:
                break
            if class_processors := cls.__dict__.get("processors"):
                logger.debug("Adding %d processors from %r", len(class_processors), cls.__name__)
                self.processors += class_processors

        logger.debug("Outlet %s has %d processors", self.name, len(self.processors), extra={"processors": self.processors})

    def __getattr__(self, name: str):
        if name == "name":
            return self.__class__.__name__
        elif name == "weight":
            return 50


    def frequency(self, dt: datetime | None) -> timedelta:
        """
        Get the frequency of the outlet.

        :param dt: Time of the article previously published.
        """
        default = timedelta(minutes=30)
        logger.debug("Outlet %r does not provide a frequency, defaulting to %s", self.name, default)

        return default


    def fetch(self, source) -> Article:
        """
        Fetch the article from the source. The source can be a URL (str or AnyHttpUrl) or an Article object.
        """

        match source:
            case str():
                if not source.startswith("http://") and not source.startswith("https://"):
                    raise ValueError(f"Cannot fetch article from non-URL string: {source!r}")
                return self.fetch_by_url(AnyHttpUrl(source))
            case AnyHttpUrl() | HttpUrl():
                return self.fetch_by_url(source)
            case Article():
                return self.fetch_by_article(source)
            case _:
                raise ValueError(f"Cannot fetch article from source of type {type(source)}")


    def fetch_by_url(self, url: AnyHttpUrl | str) -> Article:
        """
        Fetch the article from the URL.

        :param url: The URL of the article.
        """
        raise NotImplementedError("Outlet %s does not implement fetch by URL" % self.name)


    def fetch_by_article(self, article: Article) -> Article:
        """
        Fetch the article from the Article object.

        :param article: The Article object.
        """
        logger.debug("Fetching full article for %r", article, extra={"article": article, "outlet": self.name})
        url = article.get_url()
        if not url:
            raise ValueError("Article does not have a URL")
    
        # Fetch the full article, and merge it with the old one.
        # Old article might have some metadata that the fetcher does not extract.

        full_article = self.fetch(url)
        full_article.merge(article)

        return full_article


class PolynomialDelayEstimator:
    """
    A polynomial delay estimator that estimates the delay between articles based on the time of day.

    `sklearn.linear_model.LinearRegression` is used to fit a polynomial regression model to the data. The model is then
    used to estimate the delay between articles based on the time of day.
    """

    def __init__(self, coefficients: list[float], intercept: float):
        """
        Initialize the polynomial delay estimator.

        :param coefficients: The coefficients of the polynomial regression model.
        :param intercept: The intercept of the polynomial regression - i.e. the value of the polynomial when x=0.
        """
        self.coefficients = coefficients
        self.intercept = intercept

    def estimate_delay(self, minutes_since_midnight: float | int) -> float:
        # Calculate the polynomial value
        minutes_since_midnight = int(minutes_since_midnight)
        estimated_delay = self.intercept
        power = 1  # Start from x^1
        for coeff in self.coefficients:
            estimated_delay += coeff * (minutes_since_midnight ** power)
            power += 1  # Increment the power

        return estimated_delay

    def __call__(self, article_time: datetime) -> timedelta:
        # Convert current time to minutes since midnight
        y = article_time.hour * 60 + article_time.minute

        # Use the polynomial function to estimate delay
        predicted_delay = self.polynomial_delay_estimation(y)

        return timedelta(minutes=predicted_delay)


def merge_article_lists(*article_lists: Iterable[Article]) -> list[Article]:
    """
    Merge multiple lists of articles into a single list, removing duplicates based on article URLs.

    :param article_lists: Multiple iterables containing Article objects.
    :return: A single list of unique Article objects.
    """
    seen_urls = set()
    seen_ids = set()
    merged_articles = []

    for article_list in article_lists:
        for article in article_list:
            # Check if any of the article's URLs have been seen before
            if any(url.href in seen_urls for url in article.urls):
                continue  # Skip duplicate articles

            # Add the article's URLs to the seen set
            for url in article.urls:
                seen_urls.add(url.href)

            if article_id := article.meta.get("id", None) is None:
                # Check if the article's ID has been seen before
                if article_id in seen_ids:
                    logger.debug("Skipping article with duplicate ID", extra={"article_id": article_id, "article": article})
                    continue  # Skip duplicate articles

                # Add the article's ID to the seen set
                seen_ids.add(article_id)

            merged_articles.append(article)

    return merged_articles


if __name__ == "__main__":
    import logging

    from ._extractors import RssParser
    
    logging.basicConfig(level=logging.DEBUG)

    urls = [
        "https://yle.fi/rss/uutiset/paauutiset",
        "https://yle.fi/rss/uutiset/tuoreimmat",
        "https://yle.fi/rss/uutiset/luetuimmat"
    ]

    rss_feeds = [
        RssParser(url) for url in urls
    ]

    rss_articles = merge_article_lists(*rss_feeds)

    print(rss_feeds[0].feed['feed'])
    print(f"Parsed {len(rss_articles)} articles from {len(rss_feeds)} feeds")
    # for article in rss_articles:
    #     pprint(article)
