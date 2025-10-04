from structlog import get_logger
from ._extractors import TrafilaturaExtractorMixin, RssFeedMixin
from ._common import Outlet

logger = get_logger(__name__)


class Yle(TrafilaturaExtractorMixin, RssFeedMixin, Outlet):
    name = "Yle"
    valid_url = r"https://yle.fi/"
    weight = 50

    feed_urls = [
        "https://yle.fi/rss/uutiset/paauutiset",
        "https://yle.fi/rss/uutiset/tuoreimmat",
        "https://yle.fi/rss/uutiset/luetuimmat",

        "http://svenska.yle.fi/rss/senaste-nytt",
        "http://svenska.yle.fi/rss/svenskaylefi",
    ]


if __name__ == "__main__":
    from meri.utils import setup_logging
    setup_logging(debug=True)
    from pprint import pprint

    outlet = Yle()
    articles = outlet.latest()
    logger.info("Found articles", count=len(articles))

    a = outlet.fetch(articles[0])
    pprint(a.dict())