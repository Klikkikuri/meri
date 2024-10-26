from datetime import datetime, timedelta
from re import Pattern
from typing import List, Optional
from urllib.parse import ParseResult

import newspaper
from opentelemetry import trace
from pydantic import AnyHttpUrl, BaseModel, Field
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


class VestedGroup(BaseModel):
    """
    Model for identified interest group, person, or entity.
    """
    name: str = Field(..., description="Name of the group, person, or entity that has vested interest.")
    questions: List[str] = Field(
        ...,
        description="A list of questions aimed at uncovering the vested interest. Each question should explicitly identify the party and include a complete noun phrase.",
        examples=[
            "What is <individual name>'s stance as <role or affiliation> on <related issue from article>?",
            "Why might <organization name> have a vested interest in the coverage of <related issue from article>?",
            "How does <affected group> view the impact of <issue discussed in the article>?",
            "What is the potential bias of <interviewed person or organization> regarding <related issue from article>?",
            "What role does <lobbying group or business entity> play in influencing public opinion on <topic>?",
            "How does <individual or group> benefit from public perception of <issue>?",
            "Why was <organization> included in the discussion of <issue>?",
            "What possible interests does <corporation or interest group> have in the outcomes of <related topic>?",
        ],
    )
    reasoning: str = Field(..., description="Explanation of why the entity is likely to have a vested interest in the issue.")


class ArticleContext(BaseModel):
    """
    Response from the vested interest extraction model.

    Example items in `<angle brackets>` are placeholders for actual values.
    """
    wikipedia_keywords: List[str | None] = Field(
        [],
        description="List of (Wikipedia) article keywords that helps to understand the context of the article.",
        examples=[
            "Elinkeinoelämän keskusliitto",
            "Työnantajajärjestöt",
            "Kansallinen Kokoomus",
            "Jyri Häkämies",
            "Lobbaus Suomessa",
            "Kolmikantainen yhteistyö",
            "Victoria (Ruotsin kruununprinsessa)",
            "<locality>",
            "<attitude towards issue> in <locality>",
            "<notable person>",
            "<notable organization>",
            "<notable event>",
        ],
    )
    groups: List[VestedGroup] = Field([], description="List of entities identified from the article.")
