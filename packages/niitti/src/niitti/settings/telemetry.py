import os

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class TelemetrySettings(BaseModel):
    """
    Telemetry and OpenTelemetry tracing configuration model for niitti.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    enabled: bool = Field(
        default=True,
        description="Enable OpenTelemetry tracing.",
        validation_alias=AliasChoices("OTEL_TRACING_ENABLED", "TRACING_ENABLED"),
    )

    service_name: str | None = Field(
        default_factory=lambda: os.getenv("OTEL_SERVICE_NAME"),
        description="Service name for OpenTelemetry trace resources.",
        validation_alias=AliasChoices("OTEL_SERVICE_NAME", "SERVICE_NAME"),
    )

    endpoint: str | None = Field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
        description="OTLP collector endpoint URL.",
        validation_alias=AliasChoices("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_ENDPOINT"),
    )
