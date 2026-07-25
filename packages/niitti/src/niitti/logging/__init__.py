"""
Logging subpackage for niitti.

Provides structured logging initialization with OpenTelemetry context propagation.
"""

from niitti.logging.config import get_active_logging_settings, setup_logging
from niitti.logging.processors import add_opentelemetry_context

__all__ = [
    "setup_logging",
    "add_opentelemetry_context",
    "get_active_logging_settings",
]
