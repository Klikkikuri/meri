from structlog import get_logger
from ._extractors import TrafilaturaExtractorMixin
from ._common import Outlet, domain

logger = get_logger(__name__)


class Yle(TrafilaturaExtractorMixin, Outlet):
    name = "Yle"
    valid_url = domain("yle.fi")
    weight = 50
