

from datetime import datetime
from typing import Optional

from pydantic import AnyHttpUrl, BaseModel, Field
from .abc import ArticleMeta, ArticleTypeLabels, ArticleUrl, LinkLabel


class Article(BaseModel):
    """
    Article model
    """
    #id: Optional[PyObjectId] = Field(None, alias="_id")

    text: Optional[str] = Field(...)
    meta: ArticleMeta = Field(default_factory=ArticleMeta)
    labels: list[ArticleTypeLabels] = Field(default_factory=list)
    urls: list[ArticleUrl] = Field(default_factory=list)

    created_at: Optional[datetime] = Field(None)
    updated_at: Optional[datetime] = Field(None)


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

    def merge(self, other: "Article") -> "Article":
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
        for url in other.urls:
            if str(url.href) not in existing_urls:
                self.urls.append(url)

        # If no text is present, use the other article's text.
        if not self.text and other.text:
            self.text = other.text
        
        return self
