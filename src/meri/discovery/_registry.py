"""
Registry for discoverer classes.

This module provides a decorator-based registry system for registering and
retrieving discoverers by name or type.
"""

from typing import Optional, Type
from structlog import get_logger

from ._base import SourceDiscoverer

logger = get_logger(__name__)


class DiscovererRegistry:
    """
    Registry for source discoverers.
    
    Discoverers can be registered using the @register decorator or manually
    via the register_discoverer method. When multiple discoverers are registered
    for the same type, the one with the highest weight is returned.
    """
    
    def __init__(self):
        # Store list of (weight, class) tuples for each name to support multiple implementations
        self._discoverers: dict[str, list[tuple[int, Type[SourceDiscoverer]]]] = {}
    
    def register(self, name: str | list[str], weight: int = 50):
        """
        Decorator to register a discoverer class under one or more names.
        
        Usage:
            # Single name:
            @registry.register("rss")
            class RSSDiscoverer(SourceDiscoverer):
                ...
            
            # Multiple names (aliases):
            @registry.register(["rss", "feed", "atom"])
            class RSSDiscoverer(SourceDiscoverer):
                ...
            
            # With custom weight:
            @registry.register("rss", weight=80)
            class AdvancedRSSDiscoverer(SourceDiscoverer):
                ...
        
        :param name: Name(s) for the discoverer. Can be a string or list of strings.
        :param weight: Priority weight (default 50). Higher weight = higher priority.
        """
        def decorator(cls: Type[SourceDiscoverer]) -> Type[SourceDiscoverer]:
            # Ensure we have a list of names
            discoverer_names = [name] if isinstance(name, str) else name
            
            # Register under all names
            for discoverer_name in discoverer_names:
                # Register the discoverer with its weight
                if discoverer_name not in self._discoverers:
                    self._discoverers[discoverer_name] = []
                
                # Check if this exact class is already registered under this name
                for i, (existing_weight, existing_cls) in enumerate(self._discoverers[discoverer_name]):
                    if existing_cls is cls:
                        logger.warning(
                            "Discoverer class already registered under this name, updating weight",
                            name=discoverer_name,
                            class_name=cls.__name__,
                            old_weight=existing_weight,
                            new_weight=weight
                        )
                        self._discoverers[discoverer_name][i] = (weight, cls)
                        break
                else:
                    # New class for this name, add it
                    self._discoverers[discoverer_name].append((weight, cls))
                    logger.debug(
                        "Registered discoverer",
                        name=discoverer_name,
                        class_name=cls.__name__,
                        weight=weight
                    )
                
                # Sort by weight (highest first) to maintain priority order
                self._discoverers[discoverer_name].sort(key=lambda x: x[0], reverse=True)
            
            return cls
        
        return decorator
    
    def register_discoverer(
        self,
        name: str | list[str],
        discoverer_class: Type[SourceDiscoverer],
        weight: int = 50
    ):
        """
        Manually register a discoverer class under one or more names.
        
        :param name: Name(s) to register the discoverer under (string or list of strings)
        :param discoverer_class: The discoverer class to register
        :param weight: Priority weight (default 50). Higher weight = higher priority.
        """
        
        # Ensure we have a list of names
        names = [name] if isinstance(name, str) else name
        
        for discoverer_name in names:
            if discoverer_name not in self._discoverers:
                self._discoverers[discoverer_name] = []
            
            # Check if already registered under this name
            for i, (existing_weight, existing_cls) in enumerate(self._discoverers[discoverer_name]):
                if existing_cls is discoverer_class:
                    logger.warning(
                        "Discoverer already registered under this name, updating weight",
                        name=discoverer_name,
                        class_name=discoverer_class.__name__,
                        old_weight=existing_weight,
                        new_weight=weight
                    )
                    self._discoverers[discoverer_name][i] = (weight, discoverer_class)
                    break
            else:
                self._discoverers[discoverer_name].append((weight, discoverer_class))
                logger.debug(
                    "Manually registered discoverer",
                    name=discoverer_name,
                    class_name=discoverer_class.__name__,
                    weight=weight
                )
            
            # Sort by weight
            self._discoverers[discoverer_name].sort(key=lambda x: x[0], reverse=True)
    
    def get(self, name: str) -> Optional[Type[SourceDiscoverer]]:
        """
        Get a discoverer class by name (returns highest weight).
        
        :param name: Name of the discoverer
        :return: Discoverer class or None if not found
        """
        discoverers = self._discoverers.get(name)
        if not discoverers:
            return None
        # Return the highest weight (first in sorted list)
        return discoverers[0][1]
    
    def get_instance(self, name: str) -> Optional[SourceDiscoverer]:
        """
        Get an instance of a discoverer by name.
        
        :param name: Name of the discoverer
        :return: Discoverer instance or None if not found
        """
        discoverer_class = self.get(name)
        if discoverer_class is None:
            return None
        return discoverer_class()
    
    def all(self) -> dict[str, Type[SourceDiscoverer]]:
        """
        Get all registered discoverers (highest weight for each name).
        
        :return: Dictionary mapping names to discoverer classes
        """
        return {
            name: discoverers[0][1]
            for name, discoverers in self._discoverers.items()
            if discoverers
        }
    
    def list_names(self) -> list[str]:
        """
        Get a list of all registered discoverer names.
        
        :return: List of discoverer names
        """
        return list(self._discoverers.keys())
    
    def clear(self):
        """Clear all registered discoverers (useful for testing)."""
        self._discoverers.clear()
        logger.debug("Cleared all registered discoverers")
    
    def __contains__(self, name: str) -> bool:
        """Check if a discoverer is registered."""
        return name in self._discoverers and len(self._discoverers[name]) > 0
    
    def __len__(self) -> int:
        """Get the number of registered discoverers."""
        return len(self._discoverers)
    
    def invoke(self, name: str, source_url, **kwargs):
        """
        Invoke a discoverer by name with a source URL.
        
        Convenience method that combines getting an instance and calling discover().
        
        :param name: Name of the discoverer
        :param source_url: Source URL to discover from (str or HttpUrl)
        :param kwargs: Additional parameters to pass to discover()
        :return: List of discovered articles
        :raises ValueError: If discoverer not found
        
        Example:
            articles = registry.invoke("rss", "https://example.com/feed.xml")
        """
        from pydantic import HttpUrl
        
        discoverer = self.get_instance(name)
        if discoverer is None:
            available = self.list_names()
            raise ValueError(
                f"Discoverer '{name}' not found. "
                f"Available: {', '.join(available)}"
            )
        
        # Convert string URL to HttpUrl if needed
        if isinstance(source_url, str):
            source_url = HttpUrl(source_url)
        
        return discoverer.discover(source_url, **kwargs)
    
    def __repr__(self) -> str:
        names = ", ".join(self._discoverers.keys())
        return f"DiscovererRegistry({names})"


# Global registry instance
registry = DiscovererRegistry()
