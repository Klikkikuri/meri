from abc import ABC, abstractmethod

from pydantic import HttpUrl
from typing import Optional

from ..settings.newssources import NewsSource

from ..article import Article


class SourceDiscoverer(ABC):
    """Base class for discovering article URLs from news sources"""

    source: Optional[NewsSource]

    @abstractmethod
    def discover(self, source_url: HttpUrl, **kwargs) -> list[Article]:
        """Fetch latest articles from a source"""
        pass

    def set_source(self, settings: NewsSource):
        """Set the settings for this discoverer"""
        self.source = settings
        return self
