from celery import Celery

from app.config import settings

celery = Celery(
    "ingest",
    broker=settings.redis_url,
    include=["app.worker.tasks", "app.worker.digest_tasks"],
)
celery.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
