from structlog import get_logger

from ._iltapulu import _IltapuluABC

from ._common import domain

logger = get_logger(__name__)


class Iltasanomat(_IltapuluABC):
    name = "Iltasanomat"
    valid_url = [
        domain("iltasanomat.fi"),
        domain("is.fi"),
    ]
    weight = 50
