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
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    dsn: Optional[str] = Field(
        None,
        description="Sentry DSN for error tracking.",
        validation_alias="SENTRY_DSN",
    )
    environment: Optional[str] = Field(
        None,
        description="Sentry environment (e.g. 'production', 'staging').",
        validation_alias="SENTRY_ENVIRONMENT",
    )
    send_default_pii: bool = True
    traces_sample_rate: float = 0.1

    send_logs: bool = True

    openai_integration: bool = _openai_available
    otel_integration: bool = _otel_available
