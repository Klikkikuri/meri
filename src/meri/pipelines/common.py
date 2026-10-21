import json
import threading
import time
from typing import Any, ClassVar

from haystack import Pipeline
from haystack.components.builders import ChatPromptBuilder
from haystack.dataclasses import ChatMessage
from niitti import get_logger
from pydantic import BaseModel

from meri.settings import settings
from meri.settings.llms import GeneratorSettings
from meri.settings.pipelines import PipelineSettings

from ..llm import (
    get_generator,
    new_session_id,
    request_headers,
    request_slot,
    resolve_llms,
    response_details,
    structured_output_rejected,
)

logger = get_logger(__name__)


class StructuredPipeline:
    """
    Common class for pipelines utilizing pydantic models as output.

    A pipeline runs against a chain of LLMs rather than one. The chain and the retry knobs come from the
    pipeline's entry in `pipelines:`, and attempts are spent round-robin over the chain, so a dead provider
    costs one attempt instead of the whole budget.
    """

    output_model: ClassVar[type[BaseModel]]
    """The pydantic model the LLM is asked to fill, and the answer is parsed into."""

    PIPELINE_NAME: ClassVar[str] = "default"

    SETTINGS_MODEL: ClassVar[type[PipelineSettings]] = PipelineSettings
    """The model this pipeline's own entry is re-validated against. Subclasses widen it with their own fields."""

    REQUIRED_VARIABLES: ClassVar[tuple[str, ...]] = ()
    """
    Prompt variables the template cannot do without.

    Declared per pipeline rather than with `required_variables="*"`, because meri's templates are deliberately
    built from optional blocks: `{% if feedback %}` must stay optional, or an article with no reader feedback
    would fail. Name only what is genuinely mandatory.
    """

    prompt_templates: ClassVar[dict[str, str]] = {}

    def __init__(self):
        """
        Initialize the StructuredPipeline class.
        """
        # Keyed by LLM name. Built lazily: constructing a generator for a misconfigured backup provider at
        # startup would turn a healthy primary into a failure.
        self._pipelines: dict[str, Pipeline] = {}
        self._prompts: dict[str, ChatPromptBuilder] = {}
        self._lock = threading.Lock()

    def _definition(self) -> PipelineSettings:
        """
        Read this pipeline's configuration entry.

        Read lazily, never in `__init__`: `settings` is a SettingsProxy that only resolves inside a
        `bootstrap.setup()` context. A pipeline with no entry gets the defaults, which is why a name that is
        never configured, such as a test pipeline, does not fail here.

        :return: The entry, re-validated against this pipeline's own settings model.
        """
        entry = settings.pipelines.get(self.PIPELINE_NAME)
        if entry is None:
            return self.SETTINGS_MODEL()

        # The base model allows extra keys, because settings load cannot know what a pipeline declares. This is
        # where a pipeline-specific key is checked against the model that owns it.
        return self.SETTINGS_MODEL.model_validate(entry.model_dump())

    def _prompt_builder(self) -> ChatPromptBuilder:
        """
        Assemble the prompt builder from this pipeline's templates.

        Separate from `_make_pipeline` so the prompt contract can be inspected without an LLM: which variables
        the assembled template declares is a property of the templates alone.

        :return: A new prompt builder.
        """
        prompt_template = "\n\n".join(self.prompt_templates.values())

        return ChatPromptBuilder(
            [
                ChatMessage.from_system(prompt_template),
                ChatMessage.from_user("Now, please generate the response."),
            ],
            # Haystack renders an undefined variable as empty, and `run` drops any it does not declare. Two
            # silences on top of each other, so say which ones must actually arrive.
            required_variables=list(self.REQUIRED_VARIABLES) or None,
        )

    def _make_pipeline(self, llm: GeneratorSettings) -> Pipeline:
        """
        Build one prompt-builder-to-generator pipeline for a single LLM.

        Haystack forbids sharing a component between pipelines, so each LLM needs its own ChatPromptBuilder as
        well as its own generator. This is the seam the tests patch.

        :param llm: The resolved LLM to build for.
        :return: A new pipeline.
        """
        prompt = self._prompt_builder()

        # Request native structured output from the model by passing output_model to response_format
        generator = get_generator(llm, response_format=self.output_model)

        pipeline = Pipeline()
        pipeline.add_component("prompt_builder", prompt)
        pipeline.add_component("llm", generator)

        pipeline.connect("prompt_builder", "llm")

        self._prompts[llm.name] = prompt
        return pipeline

    def _pipeline_for(self, llm: GeneratorSettings) -> tuple[Pipeline, ChatPromptBuilder]:
        """
        Get the cached pipeline for one LLM, building it on first use.

        The lock covers the check and the build, because one instance is shared across the worker threads that
        process articles. It is not held over `Pipeline.run`, which would serialize the generation.

        :param llm: The resolved LLM to run against.
        :return: The pipeline and its prompt builder.
        """
        with self._lock:
            if llm.name not in self._pipelines:
                logger.debug("Building pipeline '%s' for LLM '%s'", self.PIPELINE_NAME, llm.name)
                self._pipelines[llm.name] = self._make_pipeline(llm)

            return self._pipelines[llm.name], self._prompts[llm.name]

    def _validate_output(self, model: BaseModel, prompt_vars: dict) -> BaseModel:
        """
        Check the output against the input that produced it. Raise to trigger a retry.

        :param model: The parsed output.
        :param prompt_vars: The variables the prompt was rendered with.
        :return: The output to return to the caller.
        """
        return model

    def _parse_results(self, results: dict, llm: GeneratorSettings, span: Any, prompt_vars: dict) -> BaseModel:
        """
        Read the answer of one generation and parse it into the output model.

        :param results: What `Pipeline.run` returned.
        :param llm: The LLM that answered.
        :param span: The span of the request, for the model that answered.
        :param prompt_vars: The variables the prompt was rendered with.
        :return: The validated output.
        :raises ValueError: When the answer is empty or the pipeline returned another shape.
        """
        match results:
            case {"llm": {"replies": [reply, *_]}}:
                content = reply.text
                if not content:
                    raise ValueError("Empty response from LLM")

                model_name = reply.meta.get("model", "unknown") if reply.meta else "unknown"
                span.set_attribute("model", model_name)
                # The configured name and the model the provider reports answer different questions
                # once a chain is in play, so log both.
                logger.debug(
                    "Pipeline output from LLM '%s' on model: %s",
                    llm.name,
                    model_name,
                    extra=dict(reply.meta) if reply.meta else {},
                )

                # Parse the response using the output_model
                model_output = self.output_model.model_validate_json(content)
                return self._validate_output(model_output, prompt_vars)
            case _:
                logger.error("Invalid pipeline output", extra={"pipeline": self.PIPELINE_NAME, "results": results})
                raise ValueError(f"Invalid pipeline output: {results!r}")

    def _fallback_inputs(self, prompt: ChatPromptBuilder, inputs: dict) -> dict:
        """
        Add a user message that asks for JSON and carries the output schema.

        A provider that rejects `response_format` cannot enforce the schema. The prompt still has to ask for the
        object, and `model_validate_json` still checks it.

        :param prompt: The prompt builder that the pipeline uses.
        :param inputs: The inputs of the failed request.
        :return: Inputs for a retry without native structured output.
        """
        template = list(inputs.get("template", prompt.template or []))
        template.append(
            ChatMessage.from_user(
                "Respond with one JSON object and nothing else. It must conform to this JSON schema:\n"
                "{{ json_schema }}"
            )
        )
        return {
            **inputs,
            "template": template,
            "template_variables": {
                **inputs["template_variables"],
                "json_schema": json.dumps(self.output_model.model_json_schema()),
            },
        }

    def run(
        self,
        prompt_vars: dict,
        max_retries: int | None = None,
        initial_delay: float | None = None,
        backoff_factor: float | None = None,
        messages: list[ChatMessage] | None = None,
        session_id: str | None = None,
        **kwargs,
    ) -> BaseModel:
        """Run the Haystack pipeline, falling over between the configured LLMs.

        `max_retries` is a total attempt budget spent round-robin over the chain: attempt *k* uses
        `chain[(k - 1) % len(chain)]`. With one LLM configured this is the plain retry loop it replaces.

        :param prompt_vars: Variables for the prompt template.
        :param max_retries: Total attempts before failing. Defaults to the pipeline's configuration.
        :param initial_delay: Delay in seconds before the second attempt. Defaults to the configuration.
        :param backoff_factor: Backoff multiplier per failed attempt. Defaults to the configuration.
        :param messages: A continuation of the conversation, appended after the pipeline's own prompt: the
            model's previous answer as an assistant message and the request that follows it as a user message.
            User and system messages are Jinja templates rendered with `prompt_vars`; assistant messages are
            passed through as they are.
        :param session_id: ID of the conversation. A caller that makes several calls for one conversation passes
            the same ID to each. Without it, every call starts a new conversation.
        :return: Validated output Pydantic model.
        """
        definition = self._definition()
        max_retries = definition.max_retries if max_retries is None else max_retries
        initial_delay = definition.initial_delay if initial_delay is None else initial_delay
        backoff_factor = definition.backoff_factor if backoff_factor is None else backoff_factor
        session_id = session_id or new_session_id()

        chain = resolve_llms(self.PIPELINE_NAME, settings)

        prompt_vars = {**prompt_vars, **kwargs}
        prompt_vars.setdefault("settings", settings)

        delay = initial_delay
        for attempt in range(1, max_retries + 1):
            llm = chain[(attempt - 1) % len(chain)]
            pipeline, prompt = self._pipeline_for(llm)

            # The builder declares one input socket per variable of its base template, and the pipeline checks
            # the mandatory ones are given directly. Everything else — variables only a continuation names —
            # arrives through `template_variables`, which the builder merges over the direct inputs.
            inputs: dict = {name: prompt_vars[name] for name in prompt.variables if name in prompt_vars}
            inputs["template_variables"] = prompt_vars
            if messages:
                inputs["template"] = list(prompt.template or []) + list(messages)

            if settings.logging.DEBUG:
                rendered = prompt.run(**inputs)["prompt"]
                logger.debug("Prompt for pipeline", pipeline=self.PIPELINE_NAME, messages=[m.text for m in rendered])

            headers = request_headers(llm, session_id, settings)
            try:
                with logger.span(
                    "pipeline_attempt",
                    pipeline=self.PIPELINE_NAME,
                    llm=llm.name,
                    attempt=attempt,
                    continuation=bool(messages),
                    session=session_id,
                ) as span:
                    with request_slot(llm, settings):
                        results = pipeline.run(
                            {
                                "prompt_builder": inputs,
                                **({"llm": {"generation_kwargs": {"extra_headers": headers}}} if headers else {}),
                            }
                        )

                    return self._parse_results(results, llm, span, prompt_vars)
            except Exception as exc:
                error = exc
                if structured_output_rejected(exc):
                    logger.warning(
                        "LLM '%s' rejected structured output; retrying as a JSON object",
                        llm.name,
                        **response_details(exc),
                    )
                    try:
                        with logger.span(
                            "structured_output_fallback",
                            pipeline=self.PIPELINE_NAME,
                            llm=llm.name,
                            attempt=attempt,
                        ) as fallback_span:
                            with request_slot(llm, settings):
                                results = pipeline.run(
                                    {
                                        "prompt_builder": self._fallback_inputs(prompt, inputs),
                                        "llm": {
                                            "generation_kwargs": {
                                                **({"extra_headers": headers} if headers else {}),
                                                "response_format": {"type": "json_object"},
                                            }
                                        },
                                    }
                                )
                            return self._parse_results(results, llm, fallback_span, prompt_vars)
                    except Exception as fallback_exc:  # noqa: BLE001 - the retry loop below reports it
                        error = fallback_exc

                details = response_details(error)
                if attempt < max_retries:
                    logger.warning(
                        "Pipeline '%s' attempt %d/%d on LLM '%s' failed with %s: %s. Retrying in %.1fs...",
                        self.PIPELINE_NAME,
                        attempt,
                        max_retries,
                        llm.name,
                        type(error).__name__,
                        error,
                        delay,
                        **details,
                    )
                    time.sleep(delay)
                    delay *= backoff_factor
                else:
                    logger.error(
                        "Pipeline '%s' failed after %d attempts, last on LLM '%s'",
                        self.PIPELINE_NAME,
                        max_retries,
                        llm.name,
                        exc_info=error,
                        **details,
                    )
                    raise error

        raise RuntimeError(f"Pipeline '{self.PIPELINE_NAME}' ran no attempts; max_retries was {max_retries}.")
