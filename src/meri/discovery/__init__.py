"""
Discovery module for fetching article URLs from news sources.

This module separates the concern of discovering article URLs from extracting
article content. Discoverers fetch lists of URLs from various sources like
RSS feeds, APIs, or HTML pages.

Usage:
    from meri.discovery import registry, SourceDiscoverer, RSSDiscoverer
    
    # Get a discoverer
    discoverer_class = registry.get("rss")
    
    # Get an instance
    discoverer = registry.get_instance("rss")
    
    # Invoke directly
    articles = registry.invoke("rss", "https://example.com/feed.xml")
"""

from ._base import SourceDiscoverer
from ._registry import DiscovererRegistry, registry
from ._utils import merge_article_lists
from .iltalehti import IltalehtiFeedDiscoverer
from .rss import RSSDiscoverer
from .sitemap import SitemapDiscoverer

__all__ = [
    # Base classes
    "SourceDiscoverer",
    "DiscovererRegistry",
    
    # Concrete discoverers
    "RSSDiscoverer",
    "IltalehtiFeedDiscoverer",
    "SitemapDiscoverer",
    
    # Registry instance
    "registry",
    
    # Utilities
    "merge_article_lists",
]
