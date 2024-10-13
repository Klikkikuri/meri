from datetime import datetime, timedelta
from pytz import utc
import requests
from structlog import get_logger
from ..abc import Outlet, NewspaperExtractorMixin

from ._common import PolynomialDelayEstimator

from ._processors import article_to_markdown


logger = get_logger(__name__)


class Iltalehti(NewspaperExtractorMixin, Outlet):
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

    def __init__(self) -> None:
        self.processors = [
            article_to_markdown,
        ]
        super().__init__()

    def frequency(self, dt: datetime | None) -> timedelta:
        if dt is None:
            dt = datetime.now(utc)

        return self._frequency(dt)

    def latest(self):
        latest_url = r"https://api.il.fi/v1/articles/iltalehti/lists/latest?limit=30&image_sizes[]=size138"
        base_url = r"https://www.iltalehti.fi/{category[category_name]}/a/{article_id}"

        response = requests.get(latest_url, timeout=10)
        response.raise_for_status()
        data = response.json()

        article_links = []

        for article in data["response"]:
            # Skip content that has sponsored content metadata
            if article.get("metadata", {}).get("sponsored_content", False):
                logger.debug("Skipping sponsored content: %r", article["title"])
                continue

            url = base_url.format(**article)
            article_links.append(url)

        return article_links
