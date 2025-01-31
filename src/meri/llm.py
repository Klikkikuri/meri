from enum import Enum
from importlib.resources import files
import json
import logging
from pathlib import Path
import re
from typing import  Any, Dict, List, Optional, overload

from pydantic import AnyHttpUrl, BaseModel, ValidationError

from .pydantic_llm import FORMAT_INSTRUCTIONS, PydanticOutputParser

from .settings import settings
from .abc import ArticleContext, ArticleTitleResponse, TypeResponse, VestedGroup, Article

from platformdirs import user_data_dir

from haystack import Pipeline, component
from haystack.components.generators import OpenAIGenerator
from haystack.components.builders import PromptBuilder

PROMPT_TEMPLATE_VESTED_GROUPS = "vested_groups_inst.md.j2"
PROMPT_TEMPLATE_NEWS_TYPE = "news_article_type.md.j2"
PROMPT_TEMPLATE_ARTICLE_TITLE = "artcile_title_inst.md.j2"

RE_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.MULTILINE | re.DOTALL)
""" Regular expression to extract JSON block from the response. """

logger = logging.getLogger(__name__)


class LLMGenerators(Enum):
    OPENAI = "OpenAIGenerator"


def get_prompt_template(template_name: str) -> str:
    """
    Get the prompt text based on the template name.

    Searches for the prompt template in the user data directory first, then in the package data directory.
    """

    PROMPT_ENCODING = "utf-8"

    # Hack-ish approach; append md.j2 if necessary"
    prompt_file_name = template_name
    if not template_name.endswith(".md.j2"):
        prompt_file_name += ".md.j2"
    user_prompt_dir = Path(user_data_dir(__package__), "prompts")

    user_prompt_file = user_prompt_dir / prompt_file_name
    if user_prompt_file.exists():
        return user_prompt_file.read_text(encoding=PROMPT_ENCODING)

    # Check from package data directory
    resource = f"prompts/{prompt_file_name}"
    return files(__package__).joinpath(resource).read_text(encoding=PROMPT_ENCODING)


def get_generator():
    generation_kwargs = {
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    return OpenAIGenerator(generation_kwargs=generation_kwargs)


def extract_interest_groups(article: Article) -> ArticleContext:
    return prepare_pipeline(article, ArticleContext)

def predict_article_type(article: Article) -> TypeResponse:
    return prepare_pipeline(article, TypeResponse)

def predict_article_title(article: Article) -> ArticleTitleResponse:
    return prepare_pipeline(article, ArticleTitleResponse)

def prepare_pipeline(article: Article, output_model: BaseModel):

    if settings.DEBUG:
        logging.getLogger("canals.pipeline.pipeline").setLevel(logging.DEBUG)

    template = ""
    template_vars = []

    prompt_vars = article.model_dump()
    prompt_vars["response_schema"] = output_model.schema_json(indent=2)

    if issubclass(output_model, ArticleTitleResponse):
        template = get_prompt_template(PROMPT_TEMPLATE_ARTICLE_TITLE)
    elif issubclass(output_model, TypeResponse):
        template = get_prompt_template(PROMPT_TEMPLATE_NEWS_TYPE)
    elif issubclass(output_model, ArticleContext):
        template = get_prompt_template(PROMPT_TEMPLATE_VESTED_GROUPS)
    else:
        raise ValueError("Invalid output model %s", output_model.__name__)

    # HAX: Add format instructions
    template += "\n"+FORMAT_INSTRUCTIONS

    template_vars = list(article.model_dump().keys()) + [
        # Schema validation
        "invalid_replies",
        "error_message",
        # Schema for response
        "response_schema"
    ]

    prompt_builder = PromptBuilder(template, variables=template_vars)
    llm = get_generator()
    output_validator = PydanticOutputParser(output_model)

    p = Pipeline(max_runs_per_component=5)
    p.add_component("prompt_builder", prompt_builder)
    p.add_component("llm", llm)
    p.add_component("output_validator", output_validator)

    p.connect("prompt_builder", "llm")
    p.connect("llm", "output_validator")
    p.connect("output_validator.invalid_replies", "prompt_builder.invalid_replies")
    p.connect("output_validator.error_message", "prompt_builder.error_message")

    results = p.run({
        "prompt_builder": prompt_vars
    })
    return results['output_validator']['model_output']