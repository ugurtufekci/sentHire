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
    },
    broker_connection_retry_on_startup=True,
)

celery_app.autodiscover_tasks(["senthire.workers.tasks"], force=True)
