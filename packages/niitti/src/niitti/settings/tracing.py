from typing import Optional
from pydantic import BaseModel, Field


class TracingSettings(BaseModel):
    """
    Tracing configuration model for niitti OpenTelemetry tracing system.
    """

    TRACING_ENABLED: bool = Field(
        True,
        description="Enable OpenTelemetry tracing.",
    )

    SERVICE_NAME: str = Field(
        "meri",
        description="Service name for OpenTelemetry trace resources.",
    )

    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = Field(
        None,
        description="OTLP collector endpoint URL.",
    )
