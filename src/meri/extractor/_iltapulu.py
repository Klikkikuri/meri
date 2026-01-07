
from abc import ABC
from ._common import Outlet
from ._extractors import TrafilaturaArticle, TrafilaturaExtractorMixin
from ._processors import label_paywalled_content


class IltapuluABC(TrafilaturaExtractorMixin, Outlet, ABC):

    def fetch(self, source) -> TrafilaturaArticle:
        article: TrafilaturaArticle = super().fetch(source)  # type: ignore

        article = label_paywalled_content(article)

        return article
