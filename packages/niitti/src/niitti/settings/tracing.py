from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class TracingSettings(BaseSettings):
    """
    Tracing configuration model for niitti OpenTelemetry tracing system.
    """

    TRACING_ENABLED: bool = Field(
        default=True,
        description="Enable OpenTelemetry tracing.",
        validation_alias="KLIKKIKURI_TRACING_ENABLED",
    )

    SERVICE_NAME: str = Field(
        default="meri",
        description="Service name for OpenTelemetry trace resources.",
        validation_alias="OTEL_SERVICE_NAME",
    )

    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = Field(
        default=None,
        description="OTLP collector endpoint URL.",
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )

    def get_tracing_settings(self) -> "TracingSettings":
        return TracingSettings(
            TRACING_ENABLED=self.TRACING_ENABLED,
            SERVICE_NAME=self.SERVICE_NAME,
            OTEL_EXPORTER_OTLP_ENDPOINT=self.OTEL_EXPORTER_OTLP_ENDPOINT,
        )
