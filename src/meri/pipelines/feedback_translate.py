"""
Exemplar translation for Luotsi's injection guard.

A maintainer tool for growing the labeled exemplar data into a new language. It lived in Luotsi, talking to a
chat endpoint over raw HTTP with its own API key, because Luotsi must not carry an LLM client. Meri already owns
LLM access, prompt templates and structured output, so the work belongs here and Luotsi stays a text-and-vectors
library.

The contract is deliberately plain builtins in and out: `(index, label, text)` in, `(index, text)` out. Label
semantics belong to `luotsi.guards.labeled`, which owns them, so the CLI unpacks and re-attaches them and this
module never imports LabeledLine.
"""

from typing import ClassVar, cast

from niitti import get_logger
from pydantic import BaseModel, ConfigDict

from meri.abc import TranslationResponse
from meri.prompts import get_prompt_template
from meri.settings.pipelines import PipelineSettings

from .common import StructuredPipeline

logger = get_logger(__name__)

PROMPT_TEMPLATE_TRANSLATE_EXEMPLARS = "feedback_translate_exemplars.md.j2"


class TranslateSettings(PipelineSettings):
    """
    Configuration for the exemplar translator.

    Extras are forbidden here, unlike on the base: settings load has to tolerate a key it cannot know about, but
    this is where `batch_sise: 20` should fail rather than be ignored.
    """

    model_config = ConfigDict(extra="forbid")

    batch_size: int = 20
    """Lines per request. Small batches keep a model from losing the line-to-line correspondence."""


class ExemplarTranslator(StructuredPipeline):
    """Translate labeled exemplars into another language, preserving each line's register."""

    output_model = TranslationResponse

    PIPELINE_NAME = "feedback_translate"

    SETTINGS_MODEL: ClassVar[type[PipelineSettings]] = TranslateSettings

    REQUIRED_VARIABLES = ("language", "lines")

    prompt_templates: ClassVar[dict[str, str]] = {
        "translate": get_prompt_template(PROMPT_TEMPLATE_TRANSLATE_EXEMPLARS),
    }

    def _validate_output(self, model: BaseModel, prompt_vars: dict) -> BaseModel:
        """
        Check that the answer accounts for every line, exactly once.

        A count-only check cannot catch a model that drops line 7 and duplicates line 3. That corruption keeps
        the count, so positional re-attachment would then give a translated injection the label of a different
        line and poison the guard's training data. Raising here retries through the normal path.

        :raises ValueError: When the indices are not exactly 1..n, each once.
        """
        response = cast(TranslationResponse, model)
        expected = set(range(1, len(prompt_vars["lines"]) + 1))
        seen = [line.index for line in response.lines]

        if sorted(seen) != sorted(expected):
            missing = sorted(expected - set(seen))
            extra = sorted(set(seen) - expected)
            duplicated = sorted({index for index in seen if seen.count(index) > 1})
            raise ValueError(
                f"Translation does not match the source: missing {missing}, unexpected {extra}, "
                f"duplicated {duplicated}."
            )

        return model

    def translate(self, exemplars: list[tuple[str, str]], language: str) -> list[str]:
        """
        Translate labeled exemplars, keeping their order.

        :param exemplars: The lines to translate, as `(label, text)` pairs. The label is shown to the model as
            ground truth for the line's register, and is never read back from the answer.
        :param language: Target language, named as you would name it to a translator.
        :raises ValueError: When a batch's answer does not account for every line in it.
        :return: The translated texts, in the order the exemplars were given.
        """
        batch_size = cast(TranslateSettings, self._definition()).batch_size

        translated: list[str] = []
        for start in range(0, len(exemplars), batch_size):
            batch = exemplars[start : start + batch_size]
            logger.info("Translating lines %d-%d into %s", start + 1, start + len(batch), language)

            lines = [{"label": label, "text": text} for label, text in batch]
            # Backoff and chain position reset per batch. That is correct for a tool run a few times a year, so
            # do not add stickiness between batches.
            response = cast(TranslationResponse, super().run({"language": language, "lines": lines}))

            by_index = {line.index: line.text for line in response.lines}
            translated.extend(by_index[index] for index in range(1, len(batch) + 1))

        return translated
