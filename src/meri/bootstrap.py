"""
Application bootstrap logic for meri.
"""

from contextlib import contextmanager
from typing import Iterator
import structlog

from niitti.logging import setup_logging as niitti_setup_logging
from niitti.sentry import setup_sentry as niitti_setup_sentry
from niitti.tracing import flush_tracing, setup_tracing as niitti_setup_tracing

from meri.settings.settings import Settings, clear_settings, get_settings, set_active_settings


logger = structlog.get_logger(__name__)

# Plain module global flag tracking process-wide setup activation.
# Process-wide scope (rather than ContextVar) is intentional: OpenTelemetry TracerProvider
# and standard root logging handlers are process singletons in Python.
_setup_active: bool = False


@contextmanager
def setup(
    settings: Settings | None = None,
    name: str | None = None,
    debug: bool | None = None,
) -> Iterator[Settings]:
    """
    Bootstrap application logging, OpenTelemetry tracing, and Sentry for meri.

    Instantiates/activates Settings on entry and drops/clears settings state on exit.

    :param settings: Concrete meri Settings instance. If None, instantiates default settings.
    :param name: Optional service/app name to override telemetry service_name.
    :param debug: Optional debug mode flag to override logging DEBUG setting.
    :yield: Concrete meri Settings instance.
    """
    global _setup_active

    if _setup_active:
        logger.debug(
            "setup() is already active in this process; proceeding with existing logging and telemetry configuration."
        )

    prev_setup = _setup_active
    _setup_active = True

    if settings is None:
        active = get_settings()
        if active is not None:
            settings = active
        else:
            settings = Settings()


    if debug is not None:
        settings.logging.DEBUG = debug

    set_active_settings(settings)

    telemetry = settings.telemetry
    if name:
        telemetry = telemetry.model_copy(update={"service_name": name})
    elif not telemetry.service_name:
        telemetry = telemetry.model_copy(update={"service_name": "meri"})

    niitti_setup_logging(settings.logging, force=True)
    niitti_setup_tracing(telemetry)
    niitti_setup_sentry(settings.sentry)

    try:
        yield settings
    finally:
        _setup_active = prev_setup
        if not prev_setup:
            clear_settings()
        flush_tracing()
