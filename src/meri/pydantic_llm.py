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


FORMAT_INSTRUCTIONS = r"""
## Format Instructions
{% raw %}
The output should be formatted as a JSON instance that conforms to the JSON schema below, but only return the actual instances without any additional schema definition.
As an example, for the schema `{"properties": {"foo": {"title": "Foo", "description": "a list of strings", "type": "array", "items": {"type": "string"}}}, "required": ["foo"]}`
the object `{"foo": ["bar", "baz"]}` is a well-formatted instance of the schema. The object `{"properties": {"foo": ["bar", "baz"]}}` is not well-formatted.
{% endraw %}

Here is the output schema:
```json
{{response_schema}}
```
{% if invalid_replies %}
!!!error
    Invalid Output on Previous Attempt

You already created the following output in a previous attempt:
{{invalid_replies|indent(4)}}

{% if error_message %}
However, this doesn't comply with the format requirements from above and triggered this Python exception: {{error_message|escape}}
{% endif %}
Correct the output and try again.
{% endif %}
"""

RE_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.MULTILINE | re.DOTALL)
""" Regular expression to extract JSON block from the response. """


logger = logging.getLogger(__name__)


def extract_json(response: str):
    """
    Extract JSON from response.
    """
    m = re.search(RE_JSON_BLOCK, response)
    if m:
        return m.group(1)
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
    @component.output_types(valid_replies=List[str], invalid_replies=Optional[List[str]], error_message=Optional[str], model_output=Optional[BaseModel], template=Optional[str])
    def run(self, replies: List[str]):
        self.iteration_counter += 1

        ## Try to parse the LLM's reply ##
        # If the LLM's reply is a valid object, return `"valid_replies"`
        try:
            response = extract_json(replies[0])
            if response is None:
                logger.debug("No JSON block found in the response, trying to parse the whole response.")
                # Hope that the response is valid JSON
                response = replies[0]

            json = from_json(response, allow_partial=True)
            model = self.pydantic_model.model_validate(json)

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
                self.iteration_counter, replies[0], e
            )
            return {"invalid_replies": replies[0], "error_message": str(e)}
