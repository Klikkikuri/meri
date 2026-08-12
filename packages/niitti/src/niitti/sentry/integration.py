"""
Sentry integration setup for niitti applications.
"""

from typing import Any

import structlog

from niitti.settings.sentry import SentrySettings

logger = structlog.get_logger(__name__)


def setup_sentry(
    settings: SentrySettings | None = None,
    **kwargs: Any,
) -> None:
    """
    Setup Sentry SDK integration.

    :param settings: SentrySettings model instance. If None, constructed from keyword arguments.
    :param kwargs: Keyword arguments to construct SentrySettings if settings is None.
    """
    if settings is None:
        settings = SentrySettings(**kwargs)
    if not settings.dsn:
        logger.debug("Sentry DSN not set, skipping Sentry initialization")
        return

    import sentry_sdk

    integrations = []
    if not settings.send_logs:
        try:
            from sentry_sdk.integrations.logging import LoggingIntegration

            integrations.append(LoggingIntegration(event_level=None, level=None))
        except ImportError:
            pass

    if settings.openai_integration:
        try:
            from sentry_sdk.integrations.openai import OpenAIIntegration

            integrations.append(OpenAIIntegration())
        except ImportError:
            logger.debug("OpenAIIntegration not available")

    sentry_sdk.init(
        dsn=settings.dsn,
        environment=settings.environment,
        send_default_pii=settings.send_default_pii,
        traces_sample_rate=settings.traces_sample_rate,
        integrations=integrations,
        instrumenter="otel" if settings.otel_integration else None,
    )
