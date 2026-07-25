"""
Sentry integration setup for niitti applications.
"""

import structlog

logger = structlog.get_logger(__name__)


def setup_sentry(
    dsn: str | None = None,
    environment: str | None = None,
    send_logs: bool = True,
    send_default_pii: bool = True,
    traces_sample_rate: float = 0.1,
    openai_integration: bool = False,
) -> None:
    """
    Setup Sentry SDK integration.

    :param dsn: Sentry DSN URL. If None or empty, initialization is skipped.
    :param environment: Environment name (e.g. "production", "staging").
    :param send_logs: Whether to forward Python standard logging events to Sentry.
    :param send_default_pii: Whether to attach default PII (IP address, user context).
    :param traces_sample_rate: Sample rate for performance tracing.
    :param openai_integration: Whether to enable Sentry OpenAI integration.
    """
    if not dsn:
        logger.debug("Sentry DSN not set, skipping Sentry initialization")
        return

    import sentry_sdk

    integrations = []
    if not send_logs:
        try:
            from sentry_sdk.integrations.logging import LoggingIntegration

            integrations.append(LoggingIntegration(event_level=None, level=None))
        except ImportError:
            pass

    if openai_integration:
        try:
            from sentry_sdk.integrations.openai import OpenAIIntegration

            integrations.append(OpenAIIntegration())
        except ImportError:
            logger.debug("OpenAIIntegration not available")

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        send_default_pii=send_default_pii,
        traces_sample_rate=traces_sample_rate,
        integrations=integrations,
        instrumenter="otel",
    )
