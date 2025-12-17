from structlog import get_logger

from .iltalehti import IltapuluABC

logger = get_logger(__name__)


class Iltasanomat(IltapuluABC):
    name = "Iltasanomat"
    valid_url = r"https://www.is.fi/"
    weight = 50
