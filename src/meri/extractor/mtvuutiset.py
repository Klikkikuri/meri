from structlog import get_logger

from ._common import Outlet

from ._extractors import RssFeedMixin, TrafilaturaExtractorMixin

logger = get_logger(__name__)

class MtvUutiset(TrafilaturaExtractorMixin, RssFeedMixin, Outlet):
    name = "mtvuutiset"
    valid_url = r"https://www.mtvuutiset.fi/"
    weight = 50

    feed_urls = [
        "https://www.mtvuutiset.fi/api/feed/rss/uutiset",
    ]


if __name__ == "__main__":
    from meri.utils import setup_logging
    setup_logging(debug=True)
    from pprint import pprint

    outlet = MtvUutiset()
    articles = outlet.latest()
    logger.info("Found articles", count=len(articles))

    a = outlet.fetch(articles[0])
    pprint(a.dict())
