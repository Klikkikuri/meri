"""
Haystack compatible component for retrieving pydantic model output from LLM response.
"""

# Format instructions – Copied from langchain
import logging
import re
from typing import List, Optional

from pydantic import BaseModel, ValidationError
from haystack import component
from pydantic_core import from_json

from haystack.dataclasses import ChatMessage

from meri.settings import settings


RE_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.MULTILINE | re.DOTALL)
""" Regular expression to extract JSON block from the response. """

RE_THINK_BLOCK = re.compile(r"(<think>(.*?)</think>)", re.MULTILINE | re.DOTALL)

logger = logging.getLogger(__name__)


def extract_json(response: str):
    """
    Extract JSON from response.
    """
    # Find all and 

    # m = re.search(RE_JSON_BLOCK, response)
    m = re.findall(RE_JSON_BLOCK, response)
    if m:
        return m[-1]
    return None


@component
class PydanticOutputParser:
    """
    Parse output from LLM and validate against a Pydantic model.

    Based on <https://haystack.deepset.ai/tutorials/28_structured_output_with_loop>
    """
    def __init__(self, pydantic_model: BaseModel):
        self.pydantic_model = pydantic_model
        self.iteration_counter = 0

    # Define the component output
    @component.output_types(valid_replies=List[ChatMessage], invalid_replies=Optional[List[ChatMessage]], error_message=Optional[str], model_output=Optional[BaseModel], template=Optional[str])
    def run(self, replies: List[ChatMessage]):
        self.iteration_counter += 1
        msg = replies[0].text

        ## Try to parse the LLM's reply ##
        # If the LLM's reply is a valid object, return `"valid_replies"`
        try:
            response = extract_json(msg)
            if response is None:
                logger.debug("No JSON block found in the response, trying to parse the whole response.")

                # Remove the <think> block from the response, and hope that the response is valid JSON
                response = re.sub(RE_THINK_BLOCK, "", msg)

            json = from_json(response, allow_partial=True)
            model = self.pydantic_model.model_validate(json)

            # If model is thinking model, and we're missing contemplator, add it
            if hasattr(model, "contemplator") and not model.contemplator:  # type: ignore
                # Extract the <thinking> block from the response
                think_block = re.search(RE_THINK_BLOCK, msg)
                if think_block:
                    logger.debug("Found <think> block in the response, using it as contemplator", self.iteration_counter)
                    think_text = think_block.group(2).strip()
                    # Add it to the model
                    model.contemplator = think_text  # type: ignore

            logger.debug("OutputValidator at Iteration %d: Valid JSON from LLM - No need for looping", self.iteration_counter, extra={"replies": replies})

            return {
                "valid_replies": replies,
                "model_output": model,
            }

        # If the LLM's reply is corrupted or not valid, return "invalid_replies" and the "error_message" for LLM to try again
        except (ValueError, ValidationError) as e:
            logger.warning(
                "OutputValidator at Iteration %d: Invalid JSON from LLM - Let's try again.\n"
                "Output from LLM:\n%s\n"
                "Error from OutputValidator: %s",
                self.iteration_counter, msg, e
            )
            return {"invalid_replies": msg, "error_message": str(e)}
