"""
Sulku AI-detection service settings.
"""
from pydantic import BaseModel, Field


class SulkuSettings(BaseModel):
    """
    Settings for the Sulku AI-content detection service.

    When ``enabled`` is ``False``, classification is skipped entirely and no
    network calls are made. The ``base_url`` should point to the Sulku Docker
    service (e.g. ``http://sulku:8000`` in Docker Compose).
    """

    enabled: bool = Field(True, description="Enable Sulku AI-detection.")
    base_url: str = Field("http://sulku:8000", description="Base URL of the Sulku server.")
    timeout: float = Field(10.0, description="HTTP request timeout in seconds.")
    confidence_threshold: float = Field(
        0.0,
        description=(
            "Minimum final_confidence required to apply AI_SLOP label. "
            "Set to 0.0 to rely solely on Sulku's own is_ai flag."
        ),
    )
