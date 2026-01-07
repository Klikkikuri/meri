# Return type for API parameters
from typing import (
    Any,
    Dict,
    List,
    Literal,
    NamedTuple,
    Optional,
    TypedDict,
    Union,
)


class KontioApiParams(NamedTuple):
    """
    API parameters for Kontio article extraction.
    
    Kontio API requires publication, section, and article_id to fetch full article details.

    see: :py:meth:`KontioExtractor.get_api_params` and :py:prop:`KontioExtractor.API_BASE`
    """

    publication: str
    section: str
    article_id: str

class StorylineTextAnnotations(TypedDict, total=False):
    bold: bool
    italic: bool
    uppercase: bool
    impact: bool

class StorylineTextData(TypedDict):
    content: str
    annotations: StorylineTextAnnotations

class StorylineTextBlock(TypedDict):
    type: Literal["text"]
    data: StorylineTextData

class StorylineLinkData(TypedDict):
    href: str
    content: str
    annotations: StorylineTextAnnotations

class StorylineLinkBlock(TypedDict):
    type: Literal["link"]
    data: StorylineLinkData

StorylineRichTextContent = Union[StorylineTextBlock, StorylineLinkBlock]

class StorylineRichTextData(TypedDict):
    content: List[StorylineRichTextContent]

class StorylineRichText(TypedDict):
    type: Literal["rich_text"]
    data: StorylineRichTextData

# --- Header block ---
class StorylineMediaImageDataInner(TypedDict, total=False):
    blur_hash: str
    caption: str
    height: int
    semantic_label: str
    url: str
    width: int

class StorylineMediaImageData(TypedDict):
    type: Literal["external"]
    data: StorylineMediaImageDataInner

class StorylineMedia(TypedDict):
    type: Literal["image"]
    data: StorylineMediaImageData

class StorylineHeaderAuthor(TypedDict):
    full_name: str
    avatar_url: Optional[str]

class StorylineHeaderData(TypedDict):
    paid: bool
    access_level: Literal["free", "paid"]
    style: str
    ingress: Optional[str]
    headline: str
    headline_prefix: Optional[str]
    section: str
    media: Optional[StorylineMedia]
    authors: List[StorylineHeaderAuthor]
    published_at: str
    updated_at: Optional[str]

class StorylineHeaderBlockData(TypedDict):
    variant: Literal["default"]
    data: StorylineHeaderData

class StorylineHeaderBlock(TypedDict):
    type: Literal["header"]
    data: StorylineHeaderBlockData

# --- Ad container block ---
class StorylineAdDataInner(TypedDict, total=False):
    ad_unit_id: str
    prebid_data: Dict[str, Any]

class StorylineAdData(TypedDict):
    type: Literal["ad_data"]
    data: StorylineAdDataInner

class StorylineAdContainerData(TypedDict):
    style: str
    ad: StorylineAdData

class StorylineAdContainerBlock(TypedDict):
    type: Literal["ad_container"]
    data: StorylineAdContainerData

# --- Heading block ---
class StorylineHeadingData(TypedDict):
    level: int
    content: str
    style: Optional[str]
    size: Optional[str]
    sans: bool

class StorylineHeadingBlock(TypedDict):
    type: Literal["heading"]
    data: StorylineHeadingData

# --- Union of all block types ---
StorylineBlock = Union[
    StorylineHeaderBlock,
    StorylineRichText,
    StorylineAdContainerBlock,
    StorylineHeadingBlock,
]
