from datetime import datetime
import logging
from typing import Optional
from typing_extensions import Annotated

import unicodedata
from opentelemetry import trace

from pydantic import AnyHttpUrl, BaseModel, BeforeValidator, Field, PrivateAttr, ValidationError
from .abc import ArticleLabels, ArticleMeta, ArticleTypeLabels, ArticleUrl, LinkLabel

from pydantic import computed_field

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

def title_validator(v: str) -> str:
    """
    Validate that the title is not empty or just whitespace.
    """
    v = v.strip()


    # Unicode normalization, for suspicious characters
    normalized = unicodedata.normalize("NFKC", v)
    if v != normalized:
        logger.warning("Suspicious: Title normalization doesn't match: %r -> %r", v, normalized, extra={"original": v, "normalized": normalized})

    # Check if contains non-printable characters
    if any(not c.isprintable() for c in v):
        raise ValidationError("Title contains non-printable characters")

    # Check for newline characters
    if "\n" in v or "\r" in v:
        raise ValidationError("Title cannot contain newline characters")

    if not v:
        raise ValidationError("Title cannot be empty or just whitespace")

    return v


type ArticleTitle = Annotated[str, BeforeValidator(title_validator)]

class Article(BaseModel):
    """
    Article model
    """
    _id: int = PrivateAttr(default_factory=lambda: id(object()))

    meta: ArticleMeta = Field(default_factory=ArticleMeta)
    labels: list[ArticleTypeLabels | ArticleLabels] = Field(default_factory=list)
    urls: list[ArticleUrl] = Field(default_factory=list)

    # title: Optional[ArticleTitle] = Field("")
    text: Optional[str] = Field(...)

    created_at: Optional[datetime] = Field(None)
    updated_at: Optional[datetime] = Field(None)

    @computed_field
    @property
    def title(self) -> ArticleTitle:
        """
        Get the article title from metadata.
        """
        title = self.meta.get("title", "")
        if not title:
            return ""
        return title_validator(title)
    
    @computed_field
    @property
    def href(self) -> Optional[ArticleUrl]:
        """
        Get the primary href of the article.

        If multiple URLs are present, prefer the canonical URL.
        """
        for url in self.urls:
            # Prefer canonical URL if available
            if LinkLabel.LINK_CANONICAL in url.labels:
                return url

        if self.urls:
            return self.urls[0]

        return None

    def get_url(self) -> Optional[AnyHttpUrl]:
        """
        Get the primary URL of the article.

        If multiple URLs are present, prefer the canonical URL.
        """
        if not self.urls:
            return None

        for url in self.urls:
            # Prefer canonical URL if available
            if LinkLabel.LINK_CANONICAL in url.labels:
                return AnyHttpUrl(url.href)

        return AnyHttpUrl(self.urls[0].href)

    def update(self, other: "Article") -> "Article":
        """
        Merge another article into this one.

        Uses limited heuristics to merge two articles together.
        """
        # Check that both are timezone-aware

        if not self.created_at:
            self.created_at = other.created_at
        if not self.updated_at:
            self.updated_at = other.updated_at
    
        if other.created_at:
            self.created_at = min(self.created_at, other.created_at)  # type: ignore
        if other.updated_at:
            self.updated_at = max(self.updated_at, other.updated_at)  # type: ignore

        # Merge missing metadata from stub to fetched object.
        for k, v in other.meta.items():
            if k not in self.meta or not self.meta[k]:
                self.meta[k] = v

        # Append missing URLs from stub to fetched object.
        existing_urls = set(map(lambda u: str(u.href), self.urls))
        existing_signatures = set(map(lambda u: u.signature, self.urls))
        for url in other.urls:
            if url.signature not in existing_signatures:
                existing_signatures.add(url.signature)
                self.urls.append(url)
            else:
                # Append missing labels
                for label in url.labels:
                    if label not in existing_urls:
                        existing_urls.add(label)
                continue

        # If no text is present, use the other article's text.
        if not self.text and other.text:
            self.text = other.text

        return self


    def __eq__(self, other: object) -> bool:
        """
        Compare two articles for equality based on their url signature.

        If both articles have a canonical URL, compare those.
        Otherwise, compare all URLs.
        """

        if not isinstance(other, Article):
            return super().__eq__(other)

        if self.__hash__() == other.__hash__():
            return True
 
        left = set((url.signature for url in self.urls if LinkLabel.LINK_CANONICAL in url.labels)) or set((url.signature for url in self.urls))
        right = set((url.signature for url in other.urls if LinkLabel.LINK_CANONICAL in url.labels)) or set((url.signature for url in other.urls))

        return not left.isdisjoint(right)


    # Reuse BaseModel's hash implementation, custom __eq__ disables it otherwise
    def __hash__(self) -> int:
        return hash(self._id)
