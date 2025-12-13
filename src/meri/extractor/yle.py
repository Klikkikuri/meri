from structlog import get_logger
from ._extractors import TrafilaturaExtractorMixin
from ._common import Outlet

logger = get_logger(__name__)


class Yle(TrafilaturaExtractorMixin, Outlet):
    name = "Yle"
    valid_url = r"https://yle.fi/"
    weight = 50
