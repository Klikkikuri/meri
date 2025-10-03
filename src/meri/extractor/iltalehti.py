from datetime import datetime, timedelta
from pprint import pprint
from pytz import utc
import requests
from structlog import get_logger
from ..abc import Article, ArticleMeta, ArticleTypeLabels, ArticleUrl, LinkLabel, Outlet
from ._extractors import TrafilaturaExtractorMixin

from ._common import PolynomialDelayEstimator


logger = get_logger(__name__)


class Iltalehti(TrafilaturaExtractorMixin, Outlet):
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

    def latest(self) -> list[Article]:
        latest_url = r"https://api.il.fi/v1/articles/iltalehti/lists/latest?limit=90&image_sizes[]=size138"
        base_url = r"https://www.iltalehti.fi/{category[category_name]}/a/{article_id}"

        response = requests.get(latest_url, timeout=10)
        response.raise_for_status()
        data = response.json()

        articles = []

        for article in data["response"]:

            # Skip content that has sponsored content metadata
            if article.get("metadata", {}).get("sponsored_content", False):
                logger.debug("Skipping sponsored content: %r", article["title"])
                continue
            
            urls = [ArticleUrl(href=base_url.format(**article))]
            canon_url: str = article.get("metadata", {}).get("canonical_url")
            if canon_url and canon_url not in urls:
                urls.append(ArticleUrl(href=canon_url, labels=[LinkLabel.LINK_CANONICAL]))
            else:
                urls[0].labels.append(LinkLabel.LINK_CANONICAL)

            meta = ArticleMeta({
                "title": article["title"],
                "outlet": self.name,
                "language": "fi",  # Assumption: Iltalehti is Finnish only,
                "id": str(article["article_id"]),
            })

            created_at = None
            if not (created_at := article.get("published_at")):
                logger.warning("Article missing published_at, skipping: %r", article)
                continue

            article_object = Article(
                text=article.get("lead", ""),
                urls=urls,
                meta=meta,
                created_at=datetime.fromisoformat(created_at).astimezone(utc)
            )
            if updated_at := article.get("updated_at"):
                article_object.updated_at = datetime.fromisoformat(updated_at).astimezone(utc)
            else:
                # For practical purposes, if there's no updated_at, set it to created_at
                article_object.updated_at = article_object.created_at

            # Sanity check dates
            assert article_object.created_at <= datetime.now(utc)
            assert article_object.updated_at >= article_object.created_at
            assert article_object.updated_at > (datetime.now(utc) - timedelta(days=2))
            assert article_object.updated_at not in (None, "")

            articles.append(article_object)
        return articles
