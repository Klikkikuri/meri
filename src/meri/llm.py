from enum import Enum
from importlib.resources import files
import json
import logging
from pathlib import Path
import re
from typing import  Dict, List, Optional

import newspaper
from pydantic import AnyHttpUrl, BaseModel, ValidationError

from .extractor._processors import MarkdownStr, _get_from_stack
from .settings import settings
from .abc import ArticleContext, VestedGroup

from platformdirs import user_data_dir

from haystack import Pipeline, component
from haystack.components.generators import OpenAIGenerator
from haystack.components.builders import PromptBuilder

LLM_INST_GEN_JSON = r"""
Respond by providing a reasoning first.
Then, you MUST format response following this JSON schema, but only return the actual instances without any additional schema definition:
```json
{{schema}}
```
"""

LLM_INST_USE_LANGUAGE: Dict[str, str] = {
    "en": "Write the response in English.",
    "fi": "Kirjoita vastaus suomeksi.",
}
""" Instructions for LLM to use a specific language. """

PROMPT_TEMPLATE_VESTED_GROUPS = "vested_groups_inst.md.j2"

RE_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.MULTILINE | re.DOTALL)
""" Regular expression to extract JSON block from the response. """

logger = logging.getLogger(__name__)


class LLMGenerators(Enum):
    OPENAI = "OpenAIGenerator"


def get_prompt_template(template_name) -> str:
    """
    Get the prompt text based on the template name.

    Searches for the prompt template in the user data directory first, then in the package data directory.
    """

    PROMPT_ENCODING = "utf-8"

    prompt_file_name = PROMPT_TEMPLATE_VESTED_GROUPS
    user_prompt_dir = Path(user_data_dir(__package__), "prompts")

    user_prompt_file = user_prompt_dir / prompt_file_name
    if user_prompt_file.exists():
        return user_prompt_file.read_text(encoding=PROMPT_ENCODING)

    # Check from package data directory
    resource = f"prompts/{prompt_file_name}"
    return files(__package__).joinpath(resource).read_text(encoding=PROMPT_ENCODING)


def get_generator():
    return OpenAIGenerator()


@component
class ModelOutputValidator:
    """
    Based on <https://haystack.deepset.ai/tutorials/28_structured_output_with_loop>
    """
    def __init__(self, pydantic_model: BaseModel):
        self.pydantic_model = pydantic_model
        self.iteration_counter = 0

    # Define the component output
    @component.output_types(valid_replies=List[str], invalid_replies=Optional[List[str]], error_message=Optional[str])
    def run(self, replies: List[str]):
        self.iteration_counter += 1

        ## Try to parse the LLM's reply ##
        # If the LLM's reply is a valid object, return `"valid_replies"`
        try:
            response = extract_json(replies[0])
            if response is None:
                logger.debug("No JSON block found in the response.")
                # Hope that the response is valid JSON
                response = replies[0]
            self.pydantic_model.model_validate_json(response)
            logger.debug("OutputValidator at Iteration %d: Valid JSON from LLM - No need for looping", self.iteration_counter, extra={"replies": replies})
            return {"valid_replies": replies}

        # If the LLM's reply is corrupted or not valid, return "invalid_replies" and the "error_message" for LLM to try again
        except (ValueError, ValidationError) as e:
            logger.warning(
                "OutputValidator at Iteration %d: Invalid JSON from LLM - Let's try again.\n"
                "Output from LLM:\n%s\n"
                "Error from OutputValidator: %s",
                self.iteration_counter, replies[0], e
            )
            return {"invalid_replies": replies[0], "error_message": str(e)}


def extract_interest_groups(stack: List):
    """
    Extract interest groups from text.
    """
    if settings.DEBUG:
        logging.getLogger("canals.pipeline.pipeline").setLevel(logging.DEBUG)

    template = get_prompt_template("vested_groups_inst")
    template += LLM_INST_GEN_JSON

    article = _get_from_stack(stack, newspaper.Article)
    title = article.title
    url = article.original_url
    text = _get_from_stack(stack, MarkdownStr)

    # TODO: See issue #5
    content_lang = article.meta_lang or "en"
    content_lang, *_ = content_lang.lower().split("-")
    if content_lang in LLM_INST_USE_LANGUAGE:
        template += LLM_INST_USE_LANGUAGE[content_lang]
    else:
        logger.warning("Language '%s' not supported for LLM instructions. Using English.", content_lang)

    prompt_builder = PromptBuilder(template)
    llm = get_generator()
    output_validator = ModelOutputValidator(pydantic_model=ArticleContext)

    p = Pipeline(max_runs_per_component=1)
    p.add_component("prompt_builder", prompt_builder)
    p.add_component("llm", llm)
    p.add_component(instance=output_validator, name="output_validator")

    p.connect("prompt_builder", "llm")
    p.connect("llm", "output_validator")
    p.connect("output_validator.invalid_replies", "prompt_builder.invalid_replies")
    p.connect("output_validator.error_message", "prompt_builder.error_message")

    results = p.run({
        "prompt_builder": {
            "url": str(url),
            "title": str(title),
            "text": str(text),
            "schema": ArticleContext.schema_json(indent=2),
        }
    })

    return results


def extract_json(response: str):
    """
    Extract JSON from response.
    """
    m = re.search(RE_JSON_BLOCK, response)
    if m:
        return m.group(1)
    return None
