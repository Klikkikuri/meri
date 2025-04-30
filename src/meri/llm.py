import inspect
import logging
import re
from enum import Enum
from importlib.resources import files
from pathlib import Path

from haystack import Pipeline
from haystack.components.builders import PromptBuilder
from haystack.utils.auth import Secret as HaystackSecret
from platformdirs import user_data_dir
from pydantic import BaseModel

from .abc import Article, ArticleContext, ArticleTitleResponse, TypeResponse
from .pydantic_llm import FORMAT_INSTRUCTIONS, PydanticOutputParser
from .settings import (
    Settings,
    settings,
)
from .settings.llms import GeneratorSettings

PROMPT_TEMPLATE_VESTED_GROUPS = "vested_groups_inst.md.j2"
PROMPT_TEMPLATE_NEWS_TYPE = "news_article_type.md.j2"
PROMPT_TEMPLATE_ARTICLE_TITLE = "artcile_title_inst.md.j2"

RE_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.MULTILINE | re.DOTALL)
""" Regular expression to extract JSON block from the response. """

logger = logging.getLogger(__name__)


class PipelineType(Enum):
    """
    Enum for pipeline types.
    """
    DEFAULT = "default"


def get_generator(pipeline: PipelineType = PipelineType.DEFAULT, settings: Settings = settings):
    """
    Get the generator based on the pipeline type and settings.
    
    The generator is selected based on the provider specified in the settings.
    """

    if len(settings.llm) == 0:
        raise ValueError("No LLM settings found in the configuration.")

    # from haystack.components.generators import (OpenAIGenerator)
    # return OpenAIGenerator(model="gpt-4o-mini")

    pipeline_llm: GeneratorSettings

    match pipeline:
        case PipelineType.DEFAULT:
            # Check if "default" is in the list of LLMs
            # TODO: Implement pipeline selection

            # Fall back to the first LLM in the list
            pipeline_llm = settings.llm[0]
            logger.debug("Using default LLM: %s", pipeline_llm.name)


    module, class_name = pipeline_llm._generator.rsplit(".", 1)
    # Create the generator instance
    generator_class = getattr(__import__(module, fromlist=[class_name]), class_name)
    generator_args = pipeline_llm.model_dump(exclude={"provider", "_generator", "name"})

    # Haystack has some stupid design choices that are forced upon others.
    # Check the types of the arguments, and convert to Haystack secrets if necessary.
    signature = inspect.signature(generator_class.__init__)
    for param in signature.parameters.values():
        if param.name not in generator_args: continue

        if param.annotation == HaystackSecret:
            generator_args[param.name] = HaystackSecret.from_token(generator_args[param.name])

    # Create the generator instance
    r = generator_class(**generator_args)
    return r


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

