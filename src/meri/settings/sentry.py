
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings

from importlib.util import find_spec

_otel_available: bool = find_spec("opentelemetry.exporter") is not None and find_spec("sentry_sdk") is not None
_openai_available: bool = find_spec("openai") is not None

class SentrySettings(BaseSettings):
    dsn: Optional[str] = Field(
        None,
        description="Sentry DSN for error tracking.",
        alias="SENTRY_DSN",
    )
    environment: Optional[str] = Field(
        None,
        description="Sentry environment (e.g. 'production', 'staging').",
        alias="SENTRY_ENVIRONMENT",
    )
    send_default_pii: bool = True
    traces_sample_rate: float = 0.1

    send_logs: bool = True

    openai_integration: bool = _openai_available
    otel_integration: bool = _otel_available
