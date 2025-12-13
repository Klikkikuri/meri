from structlog import get_logger

from ..discovery.rss import RSSDiscoverer

from ._common import Outlet


from ._extractors import TrafilaturaExtractorMixin

logger = get_logger(__name__)

class AksaFi(TrafilaturaExtractorMixin, Outlet):
    name = "Aksa.fi"
    valid_url = r"https://aksa.fi/"
    weight = 50


if __name__ == "__main__":
    from meri.utils import setup_logging
    setup_logging(debug=True)
    from pprint import pprint

    outlet = AksaFi()
    discover = RSSDiscoverer(["https://aksa.fi/feed/"])
    articles = outlet.latest()
    logger.info("Found articles", count=len(articles))

    a = outlet.fetch(articles[0])
    pprint(a.dict())
