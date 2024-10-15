from datetime import datetime, timedelta
from re import Pattern
from typing import Optional
from urllib.parse import ParseResult

import newspaper
from opentelemetry import trace
from pydantic import AnyHttpUrl
from structlog import get_logger

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

type UrlPattern = Pattern | AnyHttpUrl | ParseResult

# TODO: Make as pydantic model
class Outlet:
    name: Optional[str] = None
    valid_url: Pattern | list[Pattern]

    weight: Optional[int] = 50

    processors: list[callable] = []

    def __init__(self) -> None:
        self.processors = []
        # Get classes this instance is a subclass of, and add their processors
        logger.debug("Adding processors from %s", self.__class__.__name__, extra={"base_classes": self.__class__.__mro__})
        for cls in self.__class__.__mro__:
            logger.debug("Checking class %s", cls.__name__)
            if cls in [Outlet, object]:
                break
            if class_processors := cls.__dict__.get("processors"):
                logger.debug("Adding %d processors from %r", len(class_processors), cls.__name__)
                self.processors += class_processors

        logger.debug("Outlet %s has %d processors", self.name, len(self.processors), extra={"processors": self.processors})

    def __getattr__(self, name: str):
        if name == "name":
            return self.__class__.__name__
        elif name == "weight":
            return 50

    def latest(self) -> list[newspaper.Article]:
        raise NotImplementedError

    def frequency(self, dt: datetime | None) -> timedelta:
        """
        Get the frequency of the outlet.

        :param dt: Time of the article previously published.
        """
        default = timedelta(minutes=30)
        logger.debug("Outlet %r does not provide a frequency, defaulting to %s", self.name, default)

        return default

    def fetch(self, url: AnyHttpUrl) -> newspaper.Article:
        """
        Fetch the article from the URL.

        :param url: The URL of the article.
        """
        raise NotImplementedError
