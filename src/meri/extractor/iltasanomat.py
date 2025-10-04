from structlog import get_logger

from .iltalehti import _Iltapulu

from ._processors import label_paywalled_content
from ._common import Outlet
from ._extractors import RssFeedMixin, TrafilaturaArticle

logger = get_logger(__name__)


class Iltasanomat(RssFeedMixin, _Iltapulu):
    name = "Iltasanomat"
    valid_url = r"https://www.is.fi/"
    weight = 50

    feed_urls = [
        "https://www.is.fi/rss/tuoreimmat.xml",
        "http://www.iltasanomat.fi/rss/kotimaa.xml",
        "http://www.iltasanomat.fi/rss/ulkomaat.xml",
    ]
