from structlog import get_logger

from ._common import Outlet

from ._extractors import TrafilaturaExtractorMixin

logger = get_logger(__name__)

class MtvUutiset(TrafilaturaExtractorMixin, Outlet):
    name = "mtvuutiset"
    valid_url = r"https://www.mtvuutiset.fi/"
    weight = 50
