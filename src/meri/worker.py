from celery import Celery
from .settings import settings
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from celery.signals import worker_process_init

# Get package name
name, *_ = __package__.split(".")


def create_celery(name: str) -> Celery:

    # Initialize the Celery tracing
    @worker_process_init.connect
    def init_celery_tracing(*args, **kwargs):
        CeleryInstrumentor().instrument()

    # Create the Celery app
    app = Celery(name)

    # Convert values to strings
    conf = {k: str(v) for k, v in settings.celery.model_dump().items()}
    app.config_from_object(conf)

    return app


if __name__ == "__main__":
    print("Starting Celery worker", name)

    import rich
    print = rich.print

    app = create_celery(name)

    print(app.conf)

    loglevel = settings.LOGGING_LEVEL.lower()
    app.worker_main(["worker", "--loglevel", loglevel])
