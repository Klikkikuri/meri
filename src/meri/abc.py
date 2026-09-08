from datetime import UTC, datetime
from enum import Enum
from re import Pattern
from textwrap import dedent
from typing import Annotated
from urllib.parse import ParseResult

from niitti import get_logger
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
)
from typing_extensions import TypedDict

from .suola import hash_url
from .utils import clean_url

logger = get_logger(__name__)

type UrlPattern = Pattern | AnyHttpUrl | ParseResult
type PyObjectId = Annotated[str, BeforeValidator(str)]


ContemplatorType = Annotated[
    str,
    Field(
        "",
        description=dedent("""
        Describes the thought process and internal monologue of the model when generating a
        response requiring contemplation.

        The contemplator should provide insight into internal reasoning and decision-making process.

        Contemplation should be extensive, and be structured as follow:
        - Begin with small, foundational observations
        - Question each step thoroughly
        - Show natural thought progression
        - Express doubts and uncertainties
        - Revise and backtrack if you need to
        - Continue until natural resolution
        """),
        examples=[
            dedent("""
                Hmm... let me think about this...
                Wait, that doesn't seem right...
                Maybe I should approach this differently...
                Going back to what I thought earlier..."""),
            dedent("""
                Starting with the basics...
                Building on that last point...
                This connects to what I noticed earlier...
                Let me break this down further..."""
            ),
        ],
    ),
]

class DataModel(BaseModel):
    model_config = ConfigDict(
        use_attribute_docstrings=True,
    )


class LinkLabel(str, Enum):
    LINK_CANONICAL   = "com.github.klikkikuri/link-rel=canonical"
    "https://developer.mozilla.org/en-US/docs/Web/HTML/Attributes/rel#canonical"

    LINK_MOVED       = "com.github.klikkikuri/link-rel=moved"

    LINK_ALTERNATE   = "com.github.klikkikuri/link-rel=alternate"
    "https://developer.mozilla.org/en-US/docs/Web/HTML/Attributes/rel#alternate"


class ClickbaitScale(str, Enum):
    """
    Likert-type scale for ranking the clickbaitiness of an original title.
    """
    NONE = "Not Clickbait at all"
    LOW = "Slightly Clickbaity"
    MODERATE = "Moderately Clickbaity"
    HIGH = "Very Clickbaity"
    EXTREME = "Extremely Clickbaity"

class ArticleTypeLabels(str, Enum):
    """
    Labels for article types.

    Labels:
    - `com.github.klikkikuri/article-type=article`:

        A factual and concise news report that delivers essential details about a recent event or development.
        Focuses on the core questions: who, what, where, when, why, and how.

    - `com.github.klikkikuri/article-type=analysis`:

        An article that goes beyond reporting news to provide deeper insights and understanding of recent events.
        Includes background information, context, and analysis.

    - `com.github.klikkikuri/article-type=feature`:

        A creative, narrative-driven article that explores a topic, person, or event in depth.
        Includes human-interest stories, profiles, and exploratory pieces aimed at engaging the reader.

    - `com.github.klikkikuri/article-type=opinion`:

        A subjective piece offering the author's perspective, judgment, or argument on a specific topic.
        Often includes persuasive language and is intended to provoke thought or debate.

    - `com.github.klikkikuri/article-type=review`:

        A critical evaluation of a cultural or consumer product, such as a book, film, performance, or technology.
        Highlights strengths, weaknesses, and overall value to help readers form their own opinions.

    - `com.github.klikkikuri/article-type=correction`:

        A notice issued by a news organization to correct an error or inaccuracy in a previously published article.

    - `com.github.klikkikuri/article-type=press-release`:

        A formal announcement from an organization or business, crafted to inform the media and public about an event, product launch, or other newsworthy update.
        Typically promotional in nature.

    - `com.github.klikkikuri/article-type=advertisement`:

        Paid content designed to promote a product, service, or brand.

    - `com.github.klikkikuri/article-type=announcement`:

        A public notice issued by a government, organization, or authority to share important information, updates, or warnings.

    Sources:
     - https://juttutyypit.fi/juttutyypit/
    """

    TYPE_ARTICLE        = "com.github.klikkikuri/article-type=article"
    TYPE_ANALYSIS       = "com.github.klikkikuri/article-type=analysis"
    TYPE_FEATURE        = "com.github.klikkikuri/article-type=feature"
    TYPE_OPINION        = "com.github.klikkikuri/article-type=opinion"
    TYPE_REVIEW         = "com.github.klikkikuri/article-type=review"
    TYPE_CORRECTION     = "com.github.klikkikuri/article-type=correction"
    TYPE_PRESS_RELEASE  = "com.github.klikkikuri/article-type=press-release"
    TYPE_ADVERTISEMENT  = "com.github.klikkikuri/article-type=advertisement"
    TYPE_ANNOUNCEMENT   = "com.github.klikkikuri/article-type=announcement"

    # NOT USED YET
    # TYPE_MULTIMEDIA     = "com.github.klikkikuri/content-type=multimedia"
    # """
    # A video article or news segment.
    # May include news reports, interviews, documentaries, and other video content.
    # """

    # DEVELOPING_STORY    = "com.github.klikkikuri/developing-story=true"
    # """
    # Story that is still unfolding or developing.
    # """

    # AI_SLOP             = "com.github.klikkikuri/ai-slop=true"
    # """
    # Content created or significantly influenced by artificial intelligence tools, such as automated text generation or data-driven article writing.
    # """


class ArticleLabels(str, Enum):
    """
    General labels for articles.

    Labels:

    - `com.github.klikkikuri/paywalled=true`:

        The article is behind a paywall and requires a subscription or payment to access the full content.

    - `com.github.klikkikuri/sponsored=true`:

        The article is sponsored content, meaning it is paid for by an advertiser or sponsor and may have promotional intent.

    - `com.github.klikkikuri/ai-slop=true`:

        Content created or significantly influenced by artificial intelligence tools,
        such as automated text generation or data-driven article writing. Detected via
        Sulku AI-classification service.

    - `com.github.klikkikuri/has-video=true`:

        Provisional label indicating a VideoObject was found anywhere in the page metadata.

    - `com.github.klikkikuri/type=video`:

        The page is primarily video content and lacks meaningful article text

    """
    PAYWALLED  = "com.github.klikkikuri/paywalled=true"
    SPONSORED  = "com.github.klikkikuri/sponsored=true"
    AI_SLOP    = "com.github.klikkikuri/ai-slop=true"
    HAS_VIDEO  = "com.github.klikkikuri/has-video=true"
    VIDEO      = "com.github.klikkikuri/type=video"


class TitleQuorumLabel(str, Enum):
    """
    Indicates the level of agreement among LLMs when generating a title.

    Labels:
    - `com.github.klikkikuri/title-quorum=unanimous`:

        All LLMs generated the same – or very similar – title.

    - `com.github.klikkikuri/title-quorum=supermajority`:
        A significant majority of LLMs generated the same – or very similar – title.

    - `com.github.klikkikuri/title-quorum=consensus`:
        A general agreement among LLMs, but with some variation in phrasing in the generated titles.

    - `com.github.klikkikuri/title-quorum=supermajority`:
        A significant majority of LLMs generated similar titles.
    """
    UNANIMOUS = "com.github.klikkikuri/title-quorum=unanimous"
    CONSENSUS = "com.github.klikkikuri/title-quorum=consensus"
    SUPERMAJORITY = "com.github.klikkikuri/title-quorum=supermajority"

class ArticleEvidenceResponse(DataModel):
    """
    Short analysis summarizing the content, tone, and structure of the __contextual__ __article__, not the response itself. This is used to provide evidence for the clickbaitiness of the original title.
    """
    content: str = Field(..., description="Summary of the main content of the article.")
    tone: str = Field(..., description="Description of the tone of the article.")
    structure: str = Field(..., description="Description of the structure of the article.")
    reasons_for_clickbaitiness: list[str] = Field([], description="Explanation of why the original title is considered clickbait.")
    reasons_for_not_clickbaitiness: list[str] = Field([], description="Explanation of why the original title is considered not clickbait.")

class ArticleTitleResponse(DataModel):
    """
    Response model for generated headline.

    Order of fields:
     - original_title
     - evidence (evaluating original title)
     - original_title_clickbaitiness
     - contemplator (thought process for evaluating original headline and crafting a new headline)
     - suggested new headline. Example
    """
    original_title: str = Field(..., description="Original title of the article.")

    evidence: ArticleEvidenceResponse = Field(..., description="Analysis of the content, tone, and structure of the article, specifically evaluating the clickbaitiness of the original title.")

    original_title_clickbaitiness: ClickbaitScale

    contemplator: ContemplatorType

    title: str = Field(..., description="Suggested headline for the article that captures the essence of the content and follows journalistic standards, while avoiding clickbait tactics.")


class ArticleUrl(DataModel):
    """
    Article URL.
    """
    href: AnyHttpUrl = Field()
    labels: list[LinkLabel] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field
    @property
    def signature(self) -> str:
        """
        Compute a signature for the URL.
        """
        if not self.href:
            return ""
        sign = hash_url(self.href)
        return sign if sign else ""

    def __str__(self):
        return str(self.href)

    # Allow comparison with string
    def __eq__(self, other):
        if isinstance(other, str):
            return str(self.href) == other
        elif isinstance(other, ArticleUrl):
            return self.href == other.href or self.signature == other.signature
        return super().__eq__(other)


class ArticleMeta(TypedDict, total=False):
    title: str | None
    " Title of the article. Should be the same as the <title> tag in the HTML document. "
    authors: list[str] | None
    id: str | None
    language: str | None
    "Language of the article (ISO 639-1 code)."
    outlet: str | None


class TranslatedLine(DataModel):
    """One translated exemplar line. The label is not echoed: it is re-attached from the source."""

    index: int = Field(..., description="The number of the source line this translates. Copy it exactly.")
    text: str = Field(..., description="The translated text, without its label.")


class TranslationResponse(DataModel):
    """
    Response model for exemplar translation.

    There is no `contemplator` field: translation is mechanical, and the corpus being translated is a catalog of
    attack payloads that a thinking-out-loud field would only invite the model to engage with. A bare list
    cannot be the root either, because a strict JSON schema needs an object.
    """

    lines: list[TranslatedLine] = Field(..., description="One entry per source line, in any order.")


class Link(DataModel):
    labels: list[str | LinkLabel]
    url: AnyHttpUrl
    title: str


class Dataset(DataModel):
    ok: bool
    message: str

    data: list[Link]


def article_url(href: AnyHttpUrl | str, /, **kwargs) -> ArticleUrl:
    """
    Create an ArticleUrl object from a URL.
    """
    url = clean_url(str(href))
    return ArticleUrl(href=AnyHttpUrl(url), **kwargs)
