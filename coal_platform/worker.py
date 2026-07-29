from __future__ import annotations

from celery import Celery

from coal_platform.config import get_settings
from coal_platform.database import SessionLocal
from coal_platform.executor_runtime import process_queue_job
from coal_platform.ocr import build_ocr_backend
from coal_platform.sqlalchemy_store import SqlAlchemyStore
from coal_platform.storage import build_object_storage

settings = get_settings()
celery_app = Celery("coal_platform", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(task_default_queue="coal", task_track_started=True, task_serializer="json", accept_content=["json"])
object_storage = build_object_storage(settings)
ocr_backend = build_ocr_backend(settings)


def dispatch_queue_job(job_id: str) -> str:
    return str(consume_queue_job.delay(job_id).id)


@celery_app.task(bind=True, max_retries=3, name="coal_platform.consume_queue_job")
def consume_queue_job(self, job_id: str) -> dict:
    store = SqlAlchemyStore(SessionLocal)
    try:
        object_storage.initialize()
        result = process_queue_job(store, job_id, object_storage, ocr_backend, settings.ocr_dpi)
        if result is None:
            raise ValueError("queue job not found")
        return result
    except Exception as exc:
        store.retry_queue_job(job_id, {"error": {"code": "WORKER_RETRY", "message": str(exc)}})
        raise self.retry(exc=exc, countdown=min(60 * (self.request.retries + 1), 300)) from exc
