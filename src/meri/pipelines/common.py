import threading
import time
from typing import ClassVar

from haystack import Pipeline
from haystack.components.builders import ChatPromptBuilder
from haystack.dataclasses import ChatMessage
from niitti import get_logger
from pydantic import BaseModel

from meri.settings import settings
from meri.settings.llms import GeneratorSettings
from meri.settings.pipelines import PipelineSettings

from ..llm import get_generator, resolve_llms

logger = get_logger(__name__)


class StructuredPipeline:
    """
    Common class for pipelines utilizing pydantic models as output.

    A pipeline runs against a chain of LLMs rather than one. The chain and the retry knobs come from the
    pipeline's entry in `pipelines:`, and attempts are spent round-robin over the chain, so a dead provider
    costs one attempt instead of the whole budget.
    """

    output_model: BaseModel

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

    def run(
        self,
        prompt_vars: dict,
        max_retries: int | None = None,
        initial_delay: float | None = None,
        backoff_factor: float | None = None,
        **kwargs,
    ) -> BaseModel:
        """Run the Haystack pipeline, falling over between the configured LLMs.

        `max_retries` is a total attempt budget spent round-robin over the chain: attempt *k* uses
        `chain[(k - 1) % len(chain)]`. With one LLM configured this is the plain retry loop it replaces.

        :param prompt_vars: Variables for the prompt template.
        :param max_retries: Total attempts before failing. Defaults to the pipeline's configuration.
        :param initial_delay: Delay in seconds before the second attempt. Defaults to the configuration.
        :param backoff_factor: Backoff multiplier per failed attempt. Defaults to the configuration.
        :return: Validated output Pydantic model.
        """
        definition = self._definition()
        max_retries = definition.max_retries if max_retries is None else max_retries
        initial_delay = definition.initial_delay if initial_delay is None else initial_delay
        backoff_factor = definition.backoff_factor if backoff_factor is None else backoff_factor

        chain = resolve_llms(self.PIPELINE_NAME, settings)

        prompt_vars = {**prompt_vars, **kwargs}
        prompt_vars.setdefault("settings", settings)

        delay = initial_delay
        for attempt in range(1, max_retries + 1):
            llm = chain[(attempt - 1) % len(chain)]
            pipeline, prompt = self._pipeline_for(llm)

            # HACK: Haystack prompt -class bitches if it receives extra variables
            attempt_vars = {k: v for k, v in prompt_vars.items() if k in prompt.variables}

            if settings.logging.DEBUG:
                rendered = prompt.run(template_variables=attempt_vars)["prompt"][0].text
                logger.debug("Prompt for '%s': %s", self.PIPELINE_NAME, rendered)

            try:
                results = pipeline.run({
                    "prompt_builder": attempt_vars,
                })

                match results:
                    case {"llm": {"replies": [reply, *_]}}:
                        content = reply.text
                        if not content:
                            raise ValueError("Empty response from LLM")

                        model_name = reply.meta.get("model", "unknown") if reply.meta else "unknown"
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
                        return self._validate_output(model_output, attempt_vars)
                    case _:
                        logger.error("Invalid pipeline output", extra={"pipeline": pipeline, "results": results})
                        raise ValueError(f"Invalid pipeline output: {results!r}")
            except Exception as exc:
                if attempt < max_retries:
                    logger.warning(
                        "Pipeline '%s' attempt %d/%d on LLM '%s' failed with %s: %s. Retrying in %.1fs...",
                        self.PIPELINE_NAME,
                        attempt,
                        max_retries,
                        llm.name,
                        type(exc).__name__,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= backoff_factor
                else:
                    logger.exception(
                        "Pipeline '%s' failed after %d attempts, last on LLM '%s'",
                        self.PIPELINE_NAME,
                        max_retries,
                        llm.name,
                    )
                    raise

        raise RuntimeError(f"Pipeline '{self.PIPELINE_NAME}' ran no attempts; max_retries was {max_retries}.")
