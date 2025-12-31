from datetime import datetime, timedelta

from pytz import utc
from structlog import get_logger

from ._iltapulu import IltapuluABC

from ._common import PolynomialDelayEstimator, domain


logger = get_logger(__name__)


class Iltalehti(IltapuluABC):
    name = "Iltalehti"
    valid_url = domain("iltalehti.fi")
    weight = 50

    # To see how the polynomial was calculated, see the notebook `notebooks/iltalehti_delay_estimator.ipynb`
    _frequency = PolynomialDelayEstimator(
        [
            -0.7748053902642059,
            0.0020853780675782244,
            -2.455949822787182e-06,
            1.2410447589884303e-09,
            -1.9835131800927108e-13,
        ],
        109.00180688246368,
    )

    def frequency(self, dt: datetime | None) -> timedelta:
        if dt is None:
            dt = datetime.now(utc)

        return self._frequency(dt)
