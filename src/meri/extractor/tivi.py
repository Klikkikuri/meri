from structlog import get_logger

from ._iltapulu import IltapuluABC

from ._common import domain

logger = get_logger(__name__)


class Tivi(IltapuluABC):
    name = "Tivi"
    valid_url = [
        domain("tivi.fi"),
        domain("www.talouselama.fi"),
    ]
    weight = 50
