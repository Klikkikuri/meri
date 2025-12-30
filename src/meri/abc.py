from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from re import Pattern
from textwrap import dedent
from typing import Annotated, Final, List, Literal, NewType, Optional, TypeAlias, Union
from typing_extensions import TypedDict
from urllib.parse import ParseResult

from opentelemetry import trace
from pydantic import AnyHttpUrl, BaseModel, BeforeValidator, Field, WithJsonSchema, computed_field
from pydantic.json import pydantic_encoder
from structlog import get_logger

from .utils import clean_url
from .suola import hash_url

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

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
        examples=map(dedent, [
            """
            Hmm... let me think about this...
            Wait, that doesn't seem right...
            Maybe I should approach this differently...
            "Going back to what I thought earlier...
            """,
            """
            Starting with the basics...
            Building on that last point...
            This connects to what I noticed earlier...
            Let me break this down further...
            """
        ]),
    ),
]


class ConfidenceLevel(str, Enum):
    """
    Confidence level on the provided response.

    The confidence level indicates the model's certainty in the correctness of the provided response.

    - `Very Uncertain`: Very low confidence in the predicted class. The prediction is highly unreliable and is considered ambiguous.
    - `Uncertain`: Low confidence in the predicted class. The prediction should be treated with caution.
    - `Neutral`: Neither strong confidence nor strong lack of confidence in the predicted class.
    - `Certain`: The model has high confidence in the predicted class. The prediction is likely correct.
    - `Very Certain`: Very high or total confidence in the predicted class. The prediction is considered highly reliable.

    """
    LOW = "Very Uncertain"
    UNCERTAIN = "Uncertain"
    NEUTRAL = "Neutral"
    CERTAIN = "Certain"
    HIGH = "Very Certain"


class LinkLabel(str, Enum):
    LINK_CANONICAL   = "com.github.klikkikuri/link-rel=canonical"
    "https://developer.mozilla.org/en-US/docs/Web/HTML/Attributes/rel#canonical"

    LINK_MOVED       = "com.github.klikkikuri/link-rel=moved"

    LINK_ALTERNATE   = "com.github.klikkikuri/link-rel=alternate"
    "https://developer.mozilla.org/en-US/docs/Web/HTML/Attributes/rel#alternate"


class ClickbaitScale(str, Enum):
    """
    Likert-type scale for ranking the clickbaitiness of an original title.

    Clickbaits include charasteristics such as withholding information, sensationalism, and misleading cues.

    1 - `Not Clickbait at All`
        The title is straightforward, factual, and neutral, without exaggeration or emotional appeal.

    2 - `Slightly Clickbaity`
        The title is mostly factual but has a minor element of curiosity or slight exaggeration.

    3 - `Moderately Clickbaity`
        The title is somewhat sensationalized, uses emotional or vague language, but still represents the article content accurately.

    4 - `Very Clickbaity`
        The title is highly exaggerated, misleading, or sensational, often using strong emotional appeal or curiosity gaps.

    5 - `Extremely Clickbaity`
        The title is deceptive, misleading, or uses outrageous claims that do not align with the article's content.

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

    """
    PAYWALLED = "com.github.klikkikuri/paywalled=true"
    SPONSORED = "com.github.klikkikuri/sponsored=true"


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

class TypeResponse(BaseModel):
    """
    Response model for article type classification task.

    Contemplation needs to be the first field in the response.
    """
    contemplator: ContemplatorType
    types: set[tuple[ArticleTypeLabels, ConfidenceLevel]] = Field(set([]),
                                        description="List of article types identified, along with the confidence level of the prediction.",)

    # "evidence": {
    #     "content": "The article presents a detailed account of the event, including quotes from officials and eyewitnesses, and provides context and background information to inform the reader.",
    #     "tone": "The tone is neutral and informative, focusing on facts and analysis rather than promoting a specific viewpoint or agenda.",
    #     "structure": "The article follows a typical news format, presenting the who, what, when, where, why, and how of the event, without personal commentary or subjective interpretation."
    # }

class ArticleEvidenceResponse(BaseModel):
    """
    Response model for evidence extraction task.

    Short analysis summarizing the content, tone, and structure of the article.
    """
    content: str = Field(..., description="Summary of the main content of the article.")
    tone: str = Field(..., description="Description of the tone of the article.")
    structure: str = Field(..., description="Description of the structure of the article.")

class ArticleTitleResponse(BaseModel):
    """
    Response model for generated title.

    Order of fields:
     - Contemplation needs to be the first field in the response.
     - Then evidence
     - Then original title and its clickbaitiness
     - Finally the suggested title.
    """
    contemplator: ContemplatorType

    evidence: ArticleEvidenceResponse = Field(..., description="Analysis of the content, tone, and structure of the article.")

    original_title: str = Field(..., description="Original title of the article.")
    original_title_clickbaitiness: ClickbaitScale

    title: str = Field(..., description="Suggested title for the article that captures the essence of the content.")


class ArticleUrl(BaseModel):
    """
    Article URL.
    """
    href: AnyHttpUrl = Field()
    labels: list[LinkLabel] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=datetime.utcnow)

    #signature: Optional[str] = Field(None, description="Hashed signature of the URL.", alias="sign")
    @computed_field
    @property
    def signature(self) -> str:
        """
        Compute a simple signature for the URL.
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
    title: Optional[str]
    " Title of the article. Should be the same as the <title> tag in the HTML document. "
    authors: Optional[List[str]]
    id: Optional[str]
    language: Optional[str]
    "Language of the article (ISO 639-1 code)."
    outlet: Optional[str]


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


def article_url(href: AnyHttpUrl | str, /, **kwargs) -> ArticleUrl:
    """
    Create an ArticleUrl object from a URL.
    """
    url = clean_url(str(href))
    return ArticleUrl(href=AnyHttpUrl(url), **kwargs)
