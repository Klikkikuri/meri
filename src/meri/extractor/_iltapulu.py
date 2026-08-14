from abc import ABC

from ._common import Outlet
from ._extractors import TrafilaturaExtractorMixin


class IltapuluABC(TrafilaturaExtractorMixin, Outlet, ABC):
    pass
