import json
import logging
from typing import Any, ClassVar, Optional
from haystack import Pipeline
from haystack.components.builders import PromptBuilder
from opentelemetry import trace
from pydantic import BaseModel

from meri.settings import settings

from ..pydantic_llm import PydanticOutputParser

from ..llm import get_generator

logger = logging.getLogger(__name__)

class StructuredPipeline:
    """
    Common class for pipelines utilizing pydantic models as output.
    """

    pipeline: Optional[Pipeline]
    output_model: type[BaseModel]

    PIPELINE_NAME: ClassVar[str] = "default"

    prompt_templates: dict[str, str] = {}

    _prompt: PromptBuilder
    _llm: Any

    def __init__(self):
        """
        Initialize the StructuredPipeline class.
        """
        self.pipeline = None


    def _build_pipeline(self) -> Pipeline:
        """
        Build the pipeline for title generation.
        This function is called only once, and the pipeline is cached for later use.
        :return: The pipeline object.
        """
        if self.pipeline:
            logger.debug("Pipeline already built, skipping.")
            return self.pipeline

        prompt_template = "\n\n".join(self.prompt_templates.values())

        self._prompt = PromptBuilder(prompt_template)
        self._llm = get_generator(self.PIPELINE_NAME)

        self.pipeline = Pipeline(max_runs_per_component=1)
        self.pipeline.add_component("prompt_builder", self._prompt)
        self.pipeline.add_component("llm", self._llm)
        self.pipeline.add_component("output_validator", PydanticOutputParser(self.output_model))

        self.pipeline.connect("prompt_builder", "llm")

        if "response_schema" in self._prompt.variables:
            self.pipeline.connect("llm", "output_validator")
            self.pipeline.connect("output_validator.invalid_replies", "prompt_builder.invalid_replies")
            self.pipeline.connect("output_validator.error_message", "prompt_builder.error_message")
        else:
            raise ValueError("Invalid template, missing response_schema")

        return self.pipeline
    
    def run(self, prompt_vars, **kwargs) -> BaseModel:
        pipeline = self._build_pipeline()

        prompt_vars = {**prompt_vars, **kwargs}
        prompt_vars.setdefault("settings", settings)

        if "response_schema" in self._prompt.variables:
            prompt_vars["response_schema"] = json.dumps(
                self.output_model.model_json_schema(mode="serialization"),
                indent=2
            )
        else:
            raise ValueError("Invalid pipeline, missing response_schema")

        # HACK: Haystack prompt -class bitches if it receives extra variables
        prompt_vars = {k: v for k, v in prompt_vars.items() if k in self._prompt.variables}

        print(self._prompt.run(**prompt_vars)['prompt'])

        results = pipeline.run({
            "prompt_builder": prompt_vars,
        })
        match results:
            case {"output_validator": {"model_output": model_output}}:
                return model_output
            case _:
                logger.error("Invalid pipeline output", extra={"pipeline": pipeline, "results": results})
                raise ValueError("Invalid pipeline output: %r", results)
