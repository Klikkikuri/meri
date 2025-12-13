from abc import ABC, abstractmethod

from pydantic import HttpUrl

from ..article import Article


class SourceDiscoverer(ABC):
    """Base class for discovering article URLs from news sources"""

    @abstractmethod
    def discover(self, source_url: HttpUrl, **kwargs) -> list[Article]:
        """Fetch latest articles from a source"""
        pass
