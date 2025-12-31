from structlog import get_logger

from ._common import Outlet, domain

from ._extractors import TrafilaturaExtractorMixin

logger = get_logger(__name__)

class MtvUutiset(TrafilaturaExtractorMixin, Outlet):
    name = "MTV Uutiset"
    valid_url = domain("mtvuutiset.fi")
    weight = 50
