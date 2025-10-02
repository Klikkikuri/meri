from datetime import datetime, timedelta, timezone
from typing import Generator, Iterable, Optional

import fastfeedparser
from pydantic import HttpUrl
from structlog import get_logger

from meri.abc import Article, ArticleMeta, article_url

from ._processors import html_to_markdown

logger = get_logger(__name__)

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


class RssParser(Iterable[Article]):
    """
    Convert RSS feed data to a list of articles. Supports iteration over articles.

    TODO: Use own network fetching code.
    """
    feed: fastfeedparser.FastFeedParserDict
    url: HttpUrl
    language: Optional[str]

    def __init__(self, url: str | HttpUrl, language: str | None = None):
        self.url = HttpUrl(url)
        self.language = language

    def __iter__(self) -> Generator[Article, None, None]:
        """Make the parser iterable using yield."""
        self.feed = fastfeedparser.parse(str(self.url))

        for entry in self.feed.entries:

            content = entry.get("description", None).strip()
            content_lang = self.language

            # Check if "actual" - or more likely to terse - content is available
            if 'content' in entry and entry.content:
                if len(entry.content) > 1:
                    logger.warning("Multiple content entries found, using the first one.", extra={"entry": entry, "url": self.url, "feed": self.feed.feed})

                for i, content_entry in enumerate(entry.content):
                    if 'value' not in content_entry or not content_entry['value'].strip():
                        # Skip entries without 'value' key
                        logger.debug("Skipping content entry without 'value' key or empty value.", extra={"entry": entry, "index": i, "url": self.url, "feed": self.feed.feed})
                        continue

                    _content = content_entry['value'].strip()

                    # TODO: Maybe implement language filtering here?
                    content_lang = content_entry.get("language", content_lang)

                    mime_type = content_entry.get("type", "").strip()
                    match mime_type:
                        case "text/plain":
                            _content = content_entry['value'].strip()
                        case "text/html":
                            _content = html_to_markdown(_content)
                        case _:
                            logger.warning("Unknown content type, skipping.", extra={"entry": entry, "index": i, "url": self.url, "feed": self.feed.feed})
                            continue
                    break

            url = entry.get("link", "").strip()
            if not url:
                logger.warning("Entry %r missing url (`link` -field), skipping.", entry['title'], extra={"entry": entry, "url": self.url, "feed": self.feed.feed})
                continue

            published = None
            if _published := entry.get("published", None):
                published = datetime.fromisoformat(_published)
            else:
                published = datetime.now(timezone.utc)

            article = Article(
                text=content,
                meta=ArticleMeta({
                    "title": entry.get("title", "").strip(),
                    "language": content_lang,
                    "authors": [author.strip() for author in entry.get("author", "").split(",") if author.strip()],
                    "id": entry.get("id", entry.get("link", "")).strip(),
                }),
                created_at=published,
                urls=[article_url(url)]
            )

            yield article

    def parse(self) -> list[Article]:
        """Parse and return all articles as a list."""
        return list(self)


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
    print(f"Parsed {len(rss_articles)} articles from {len(rss_feeds)} feeds")
    # for article in rss_articles:
    #     pprint(article)
