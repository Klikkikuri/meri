from importlib.util import find_spec
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

_otel_available: bool = find_spec("opentelemetry.exporter") is not None and find_spec("sentry_sdk") is not None
_openai_available: bool = find_spec("openai") is not None


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
