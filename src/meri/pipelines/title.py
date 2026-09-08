"""
Pipeline to predict the title of an article.
"""

from typing import ClassVar

from haystack import Document
from niitti import get_logger

from meri.abc import ArticleTitleResponse
from meri.prompts import (
    PROMPT_TEMPLATE_ARTICLE,
    PROMPT_TEMPLATE_ARTICLE_TITLE,
    PROMPT_TEMPLATE_ARTICLE_UPDATED,
    PROMPT_TEMPLATE_FEEDBACK,
    get_prompt_template,
)
from meri.settings import settings

from .common import StructuredPipeline

logger = get_logger(__name__)

class TitlePredictor(StructuredPipeline):

    output_model = ArticleTitleResponse

    PIPELINE_NAME = "title"

    # `feedback` and `rahti` are deliberately absent: both are optional blocks in the template.
    REQUIRED_VARIABLES = ("text", "meta")

    prompt_templates: ClassVar[dict[str, str]] = {
        "article": get_prompt_template(PROMPT_TEMPLATE_ARTICLE),
        "article_title": get_prompt_template(PROMPT_TEMPLATE_ARTICLE_TITLE),
        "previous_title": get_prompt_template(PROMPT_TEMPLATE_ARTICLE_UPDATED),
        "feedback": get_prompt_template(PROMPT_TEMPLATE_FEEDBACK),
    }

    def run(self, article, context: list[Document] | None = None, **kwargs):

        prompt_vars = kwargs.copy()
        prompt_vars.update(article.model_dump())

        prompt_vars["context"] = context or []
        prompt_vars["article"] = article
        prompt_vars["settings"] = settings

        return super().run(prompt_vars)

if __name__ == "__main__":
    import sys

    from meri.bootstrap import setup

    with setup():
        from meri.extractor._extractors import trafilatura_extractor
        from meri.scraper import try_setup_requests_cache

        try_setup_requests_cache()

    url = sys.argv[1]
    if not url:
        raise ValueError("URL is required")

    article = trafilatura_extractor(url)
    print(article.text[0:200], "...", "\n", "...", article.text[-200:])

    title = TitlePredictor()
    print(title.run(article))
