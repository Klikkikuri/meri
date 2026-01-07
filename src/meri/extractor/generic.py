
import re

from ._extractors import TrafilaturaExtractorMixin

from ._common import Outlet


class TrafilaturaExtractor(TrafilaturaExtractorMixin, Outlet):
    name = "trafilatura"
    valid_url = re.compile(r"https?://(www\.)?[^\.]+\.[^/]+/.*")
    weight = 20
