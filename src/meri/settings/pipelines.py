"""
Pipeline definitions.

A pipeline definition says which LLMs a pipeline may use and how hard it tries. Pipeline-specific tunables live
with the pipeline that owns them: each `StructuredPipeline` declares a `SETTINGS_MODEL` and re-validates its own
entry against it. This module must not import them, because `meri.settings` cannot depend on `meri.pipelines`.
"""

from pydantic import BaseModel, ConfigDict, Field


class UnknownLLMError(ValueError):
    """Raised when a pipeline definition names an LLM that `llm:` does not configure."""


class PipelineSettings(BaseModel):
    """
    Common pipeline configuration. Subclasses add the fields their pipeline understands.

    Extra keys are allowed here because settings load cannot know what a pipeline declares: validating them
    would mean importing the pipeline classes. A subclass sets `extra="forbid"`, so the re-validation at pipeline
    construction is what rejects a typo.
    """

    model_config = ConfigDict(extra="allow")

    llm: list[str] = Field(
        default_factory=list,
        description="Names from `llm:`, tried in order. Empty means every configured LLM, in configuration order.",
    )
    max_retries: int = Field(default=3, description="Total attempts, spent round-robin over the LLM chain.")
    initial_delay: float = Field(default=1.0, description="Delay in seconds before the second attempt.")
    backoff_factor: float = Field(default=2.0, description="Multiplier applied to the delay after each attempt.")
