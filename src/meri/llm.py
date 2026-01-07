import inspect
import logging
import re
from enum import Enum
from importlib.resources import files
from pathlib import Path

from haystack.utils.auth import Secret as HaystackSecret
from platformdirs import user_data_dir

from .settings import (
    Settings,
    settings,
)
from .settings.llms import GeneratorSettings

PROMPT_TEMPLATE_VESTED_GROUPS = "vested_groups_inst.md.j2"
PROMPT_TEMPLATE_NEWS_TYPE = "news_article_type.md.j2"
PROMPT_TEMPLATE_ARTICLE_TITLE = "artcile_title_inst.md.j2"
PROMPT_TEMPLATE_OUTPUT_FORMAT = "output_format_json.md.j2"
PROMPT_TEMPLATE_ARTICLE = "article.md.j2"
PROMPT_TEMPLATE_ARTICLE_UPDATED = "article_updated.md.j2"

RE_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.MULTILINE | re.DOTALL)
""" Regular expression to extract JSON block from the response. """


logger = logging.getLogger(__name__)


class UnknownPipelineType(ValueError):
    """
    Exception raised when an unknown pipeline type is encountered.
    """
    pass

class PipelineType(Enum):
    """
    Enum for pipeline types.
    """
    DEFAULT = "default"


def get_generator(pipeline: PipelineType = PipelineType.DEFAULT, settings: Settings = settings, **kwargs) -> object:
    """
    Get the generator based on the pipeline type and settings.
    
    The generator is selected based on the provider specified in the settings.
    """

    if len(settings.llm) == 0:
        raise ValueError("No LLM settings found in the configuration.")

    pipeline_llm: GeneratorSettings

    # FIXME: Use the default LLM always
    pipeline = PipelineType.DEFAULT

    match pipeline:
        case PipelineType.DEFAULT:
            # Check if "default" is in the list of LLMs
            # TODO: Implement pipeline selection

            # Fall back to the first LLM in the list
            pipeline_llm = settings.llm[0]
            logger.debug("Using default LLM: %s", pipeline_llm.name)
        case _:
            raise UnknownPipelineType(f"Unknown pipeline type: {pipeline}")


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

    # Deep merge with any additional kwargs
    if kwargs:
        generator_args.setdefault("generation_kwargs", {})
        generator_args["generation_kwargs"].update(kwargs)

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
