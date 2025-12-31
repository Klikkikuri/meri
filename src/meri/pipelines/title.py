"""
Pipeline to predict the title of an article.
"""

import logging
from typing import List

from haystack import Document

from meri.abc import ArticleTitleResponse
from meri.llm import (
    PROMPT_TEMPLATE_ARTICLE,
    PROMPT_TEMPLATE_ARTICLE_TITLE,
    PROMPT_TEMPLATE_ARTICLE_UPDATED,
    PROMPT_TEMPLATE_OUTPUT_FORMAT,
    get_prompt_template,
)
from meri.settings import settings

from .common import StructuredPipeline

logger = logging.getLogger(__name__)

class TitlePredictor(StructuredPipeline):

    output_model = ArticleTitleResponse

    PIPELINE_NAME = "title"

    prompt_templates: dict[str, str] = {
        "article": get_prompt_template(PROMPT_TEMPLATE_ARTICLE),
        "article_title": get_prompt_template(PROMPT_TEMPLATE_ARTICLE_TITLE),
        "previous_title": get_prompt_template(PROMPT_TEMPLATE_ARTICLE_UPDATED),
        "output_format": get_prompt_template(PROMPT_TEMPLATE_OUTPUT_FORMAT),
    }

    def run(self, article, context: List[Document] = [], **kwargs):

        prompt_vars = kwargs.copy()
        prompt_vars.update(article.model_dump())

        prompt_vars["context"] = context
        prompt_vars["article"] = article
        prompt_vars["settings"] = settings

        return super().run(prompt_vars)

if __name__ == "__main__":
    import logging
    import sys

    import rich

    from meri.utils import setup_logging

    setup_logging()
    #logging.basicConfig(level=logging.INFO)

    from meri.extractor._extractors import trafilatura_extractor
    from meri.scraper import try_setup_requests_cache

    try_setup_requests_cache()
    url = sys.argv[1]
    if not url:
        raise ValueError("URL is required")

    article = trafilatura_extractor(url)
    print(article.text[0:200], "...", "\n", "...", article.text[-200:])

    title = TitlePredictor()
    rich.print(title.run(article))
