import logging

from meri.abc import TypeResponse
from meri.llm import (
    PROMPT_TEMPLATE_ARTICLE,
    PROMPT_TEMPLATE_NEWS_TYPE,
    PROMPT_TEMPLATE_OUTPUT_FORMAT,
    get_prompt_template,
)
from meri.settings import settings

from .common import StructuredPipeline

logger = logging.getLogger(__name__)
 

class TypePredictor(StructuredPipeline):

    output_model = TypeResponse

    PIPELINE_NAME = "type"

    prompt_templates: dict[str, str] = {
        "article_title": get_prompt_template(PROMPT_TEMPLATE_NEWS_TYPE),
        "article": get_prompt_template(PROMPT_TEMPLATE_ARTICLE),
        "output_format": get_prompt_template(PROMPT_TEMPLATE_OUTPUT_FORMAT),
    }

    def run(self, article):

        prompt_vars = article.model_dump()

        prompt_vars["article"] = article
        prompt_vars["settings"] = settings

        return super().run(prompt_vars)
