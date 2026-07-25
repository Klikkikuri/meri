from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class TracingSettings(BaseModel):
    """
    Tracing configuration model for niitti OpenTelemetry tracing system.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    TRACING_ENABLED: bool = Field(
        default=True,
        description="Enable OpenTelemetry tracing.",
        validation_alias="KLIKKIKURI_TRACING_ENABLED",
    )

    SERVICE_NAME: Optional[str] = Field(
        default=None,
        description="Service name for OpenTelemetry trace resources.",
        validation_alias="OTEL_SERVICE_NAME",
    )

    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = Field(
        default=None,
        description="OTLP collector endpoint URL.",
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )
