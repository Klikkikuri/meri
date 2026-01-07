"""
Haystack compatible component for retrieving pydantic model output from LLM response.
"""

# Format instructions – Copied from langchain
import logging
from pprint import pprint
import re
from typing import List, Optional

from pydantic import BaseModel, ValidationError
from haystack import component
from pydantic_core import from_json

from haystack.dataclasses import ChatMessage

from meri.settings import settings


RE_JSON_BLOCK = re.compile(r"```json\s*(.*?)\s*```", re.MULTILINE | re.DOTALL)
""" Regular expression to extract JSON block from the response. """

RE_THINK_BLOCK = re.compile(r"(<think>(.*?)</think>)", re.MULTILINE | re.DOTALL)

RE_CONTROL_CHARS = re.compile(r"[\x00-\x1F\x7F-\x9F]+")
""" Regular expression to match control characters. """

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

def remove_control_chars(text: str) -> str:
    """
    Remove control characters (non-printing characters) from a string.

    Some LLM's like to insert control characters in their output, which can
    break JSON parsing. They can be safely removed – we are not controlling
    printers here.
    """
    #return re.sub(r"[\x00-\x1F\x7F-\x9F]", "", s)

    matches = list(RE_CONTROL_CHARS.finditer(text))
    if matches:
        _logger = logger.getChild("remove_control_chars")

        _logger.info("Removed control characters from LLM output: %r", [m.group(0) for m in matches], extra={"num_removed": len(matches)})

        # # Show some context around the removed characters
        # if settings.DEBUG:
        #     window = 10  # Number of characters to show around the bad character
        #     for m in matches:
        #         for match in matches:
        #             char_hex = hex(ord(match.group()))
        #             start_pos = match.start()
                    
        #             # Calculate a window of text around the character
        #             snippet_start = max(0, start_pos - window)
        #             snippet_end = min(len(text), start_pos + window + 1)

        #             # Use repr() on the snippet so the bad character is visible as a code
        #             context_snippet = repr(text[snippet_start:snippet_end])

        #             _logger.debug(f"Char {char_hex} at index {start_pos} inside snippet: {context_snippet}")
        return RE_CONTROL_CHARS.sub("", text)
    return text

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

        if settings.DEBUG:
            pprint(replies, width=120, compact=True)

        msg = replies[0].text

        if not msg:
            logger.warning("OutputValidator at Iteration %d: Empty response from LLM - Let's try again.", self.iteration_counter)
            return {"invalid_replies": msg, "error_message": "Empty response from LLM."}


        ## Try to parse the LLM's reply ##
        # If the LLM's reply is a valid object, return `"valid_replies"`
        try:
            response = extract_json(msg)
            if response is None:
                logger.debug("No JSON block found in the response, trying to parse the whole response.")

                # Remove the <think> block from the response, and hope that the response is valid JSON
                response = RE_THINK_BLOCK.sub("", msg)

            model: BaseModel
            try:
                json = from_json(response, allow_partial=True)
                model = self.pydantic_model.model_validate(json)
            except (ValueError, ValidationError) as e:
                logger.debug("Failed to parse JSON block, trying to remove control characters and parse again: %s", e)
                response = remove_control_chars(response)
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
            logger.debug("Response:\n%s", msg)
            return {"invalid_replies": msg, "error_message": str(e)}
