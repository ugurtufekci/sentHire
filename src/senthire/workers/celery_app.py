from celery import Celery

from senthire.config import get_settings

settings = get_settings()

celery_app = Celery("senthire", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_acks_late=True,  # redelivery-safe: tasks are idempotent (docs/08 §3)
    worker_prefetch_multiplier=1,
    task_default_queue="parse",
    task_routes={
        "senthire.intake.*": {"queue": "parse"},
        "senthire.screen.*": {"queue": "screen"},
        "senthire.poll.*": {"queue": "poll"},
        "senthire.mail.*": {"queue": "mail"},
    },
    broker_connection_retry_on_startup=True,
)

# Task registration happens by importing the package: tasks/__init__.py walks
# its own modules. (autodiscover_tasks looked like it did this, but it searches
# for a `tasks` module *inside* each named package — senthire.workers.tasks.tasks
# — which does not exist, so it registered nothing and the imports in __init__
# were doing all the real work.)
import senthire.workers.tasks  # noqa: E402, F401
