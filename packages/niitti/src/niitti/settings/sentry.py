from importlib.util import find_spec
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
import structlog

logger = structlog.get_logger(__name__)


def _is_module_available(name: str) -> bool:
    """
    Safely check if a module is available without raising errors on malformed parent/namespace packages.
    """
    try:
        return find_spec(name) is not None
    except (ModuleNotFoundError, ValueError, AttributeError, ImportError) as e:
        # Log caught find_spec exceptions via structlog to prevent silent dropping of errors
        logger.debug("Could not find spec for module", module_name=name, error=str(e), exc_info=True)
        return False


_otel_available: bool = _is_module_available("opentelemetry.exporter") and _is_module_available("sentry_sdk")
_openai_available: bool = _is_module_available("openai")


class SentrySettings(BaseModel):
    """
    Sentry configuration model for error tracking and performance monitoring.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    dsn: Optional[str] = Field(
        default=None,
        description="Sentry DSN for error tracking.",
        validation_alias="SENTRY_DSN",
    )
    environment: Optional[str] = Field(
        default=None,
        description="Sentry environment (e.g. 'production', 'staging').",
        validation_alias="SENTRY_ENVIRONMENT",
    )
    send_default_pii: bool = Field(
        default=False,
        description="Enable or disable sending personally identifiable information (PII) like IP addresses.",
        validation_alias="SENTRY_SEND_DEFAULT_PII",
    )
    traces_sample_rate: float = Field(
        default=0.1,
        description="Sample rate for Sentry tracing (0.0 to 1.0).",
        validation_alias="SENTRY_TRACES_SAMPLE_RATE",
    )

    send_logs: bool = Field(
        default=True,
        description="Whether to send application logs to Sentry as breadcrumbs or events.",
        validation_alias="SENTRY_SEND_LOGS",
    )

    openai_integration: bool = Field(
        default=_openai_available,
        description="Enable Sentry OpenAI integration for tracking LLM calls.",
        validation_alias="SENTRY_OPENAI_INTEGRATION",
    )
    otel_integration: bool = Field(
        default=_otel_available,
        description="Enable Sentry OpenTelemetry integration.",
        validation_alias="SENTRY_OTEL_INTEGRATION",
    )
