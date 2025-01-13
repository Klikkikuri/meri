from datetime import datetime, timedelta
from enum import Enum
from re import Pattern
from typing import Annotated, List, Optional
from typing_extensions import TypedDict
from urllib.parse import ParseResult

import newspaper
from opentelemetry import trace
from pydantic import AnyHttpUrl, BaseModel, BeforeValidator, Field
from structlog import get_logger

from .utils import clean_url

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

type UrlPattern = Pattern | AnyHttpUrl | ParseResult
type PyObjectId = Annotated[str, BeforeValidator(str)]

type ContemplatorType = Annotated[
    List[str],
    Field(description="""
        - Begin with small, foundational observations
        - Question each step thoroughly
        - Show natural thought progression
        - Express doubts and uncertainties
        - Revise and backtrack if you need to
        - Continue until natural resolution
        """,
        examples=[
            [
                "Hmm... let me think about this...",
                "Wait, that doesn't seem right...",
                "Maybe I should approach this differently...",
                "Going back to what I thought earlier..."
            ], [
                "Starting with the basics...",
                "Building on that last point...",
                "This connects to what I noticed earlier...",
                "Let me break this down further..."
            ]
        ]
    )
]

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


class LinkLabel(str, Enum):
    LINK_CANONICAL   = "com.github.klikkikuri/link-rel=canonical"
    "https://developer.mozilla.org/en-US/docs/Web/HTML/Attributes/rel#canonical"

    LINK_MOVED       = "com.github.klikkikuri/link-rel=moved"

    LINK_ALTERNATE   = "com.github.klikkikuri/link-rel=alternate"
    "https://developer.mozilla.org/en-US/docs/Web/HTML/Attributes/rel#alternate"


class ArticleTypeLabels(str, Enum):
    """
    Labels for article types.
    """

    TYPE_ARTICLE        = "com.github.klikkikuri/article-type=article"
    """
    A factual and concise news report that delivers essential details about a recent event or development. 
    Focuses on the core questions: who, what, where, when, why, and how.
    """

    TYPE_ANALYSIS       = "com.github.klikkikuri/article-type=analysis"
    """
    An article that goes beyond reporting news to provide deeper insights and understanding of recent events.
    Includes background information, context, and analysis.
    """

    TYPE_FEATURE        = "com.github.klikkikuri/article-type=feature"
    """
    A creative, narrative-driven article that explores a topic, person, or event in depth.  and profiles (for example, an article about a movie actor starring in a recently-released film).
    Includes human-interest stories, profiles, and exploratory pieces aimed at engaging the reader.
    """

    TYPE_OPINION        = "com.github.klikkikuri/article-type=opinion"
    """
    A subjective piece offering the author's perspective, judgment, or argument on a specific topic. 
    Often includes persuasive language and is intended to provoke thought or debate.
    """

    TYPE_REVIEW         = "com.github.klikkikuri/article-type=review"
    """
    A critical evaluation of a cultural or consumer product, such as a book, film, performance, or technology. 
    Highlights strengths, weaknesses, and overall value to help readers form their own opinions.
    """

    TYPE_PRESS_RELEASE  = "com.github.klikkikuri/article-type=press-release"
    """
    A formal announcement from an organization or business, crafted to inform the media and public about an event, product launch, or other newsworthy update. 
    Typically promotional in nature.
    """

    TYPE_ADVERTISEMENT  = "com.github.klikkikuri/article-type=advertisement"
    """
    Paid content designed to promote a product, service, or brand.
    """

    TYPE_ANNOUNCEMENT = "com.github.klikkikuri/article-type=announcement"
    """
    A public notice issued by a government, organization, or authority to share important information, updates, or warnings. 
    """

    TYPE_MULTIMEDIA     = "com.github.klikkikuri/article-type=multimedia"
    """
    A video article or news segment. 
    May include news reports, interviews, documentaries, and other video content.
    """

    AI_SLOP             = "com.github.klikkikuri/ai-slop=true"
    """
    Content created or significantly influenced by artificial intelligence tools, such as automated text generation or data-driven article writing. 
    """


class TypeResponse(BaseModel):
    contemplator: ContemplatorType
    types: List[ArticleTypeLabels] = Field([])

    # "evidence": {
    #     "content": "The article presents a detailed account of the event, including quotes from officials and eyewitnesses, and provides context and background information to inform the reader.",
    #     "tone": "The tone is neutral and informative, focusing on facts and analysis rather than promoting a specific viewpoint or agenda.",
    #     "structure": "The article follows a typical news format, presenting the who, what, when, where, why, and how of the event, without personal commentary or subjective interpretation."
    # }


class ArticleUrl(BaseModel):
    """
    Article URL.
    """
    href: AnyHttpUrl = Field(...)
    labels: list[LinkLabel] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=datetime.utcnow)

    def __str__(self):
        return self.href


class ArticleMeta(TypedDict, total=False):
    title: Optional[str]
    " Title of the article. Should be the same as the <title> tag in the HTML document. "
    authors: List[str] = Field(default_factory=list)
    id: Optional[str]
    language: Optional[str]
    "Language of the article (ISO 639-1 code)."


class Article(BaseModel):
    """
    Article model
    """
    #id: Optional[PyObjectId] = Field(None, alias="_id")

    text: str = Field(...)
    meta: ArticleMeta = Field(default_factory=ArticleMeta)
    labels: list[ArticleTypeLabels] = Field(default_factory=list)
    urls: list[ArticleUrl] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


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
            "Why is <interviewed person or organization> in the news?",
            "What possible interests does <corporation or interest group> have in the outcomes of <related topic>?",
        ],
    )
    reasoning: str = Field(..., description="Explanation of why the entity is likely to have a vested interest in the issue.")


class ArticleContext(BaseModel):
    """
    Response from the vested interest extraction model.

    If the article content is missing, too short or not suitable in any other way, the :attr:`ok` field should be `False`.

    `<angle brackets>` in examples indicate placeholders for actual values.
    """

    reasoning: str = Field(..., description="Message detailing the reasoning.")
    ok: bool = Field(..., description="Flag to indicate if the extraction was successful.")

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


class Link(BaseModel):
    labels: list[str | LinkLabel]
    url: AnyHttpUrl
    title: str


class Dataset(BaseModel):
    ok: bool
    message: str
    
    data: list[Link]


def article_url(href: AnyHttpUrl, /, **kwargs) -> ArticleUrl:
    """
    Create an ArticleUrl object from a URL.
    """
    url = clean_url(href)
    return ArticleUrl(href=url, **kwargs)
