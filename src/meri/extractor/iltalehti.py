from datetime import datetime, timedelta

from pytz import utc
from structlog import get_logger

from ._common import Outlet, PolynomialDelayEstimator
from ._extractors import TrafilaturaArticle, TrafilaturaExtractorMixin
from ._processors import label_paywalled_content


logger = get_logger(__name__)

class IltapuluABC(TrafilaturaExtractorMixin, Outlet):

    def fetch(self, source) -> TrafilaturaArticle:
        article: TrafilaturaArticle = super().fetch(source)  # type: ignore

        article = label_paywalled_content(article)

        return article


class Iltalehti(IltapuluABC):
    name = "Iltalehti"
    valid_url = r"//www.iltalehti.fi/"
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

    def fetch(self, source) -> TrafilaturaArticle:
        article: TrafilaturaArticle = super().fetch(source)  # type: ignore

        article = label_paywalled_content(article)

        return article
