from dataclasses import asdict

import httpx
from google.genai.errors import APIError

from app.config import settings
from app.core import delivery, digest_agent, digest_store as store
from app.core.profiles import Profile, fetch_profiles, segment_profiles
from app.worker.celery_app import celery

RETRYABLE = (ConnectionError, APIError, httpx.HTTPError)


@celery.task(name="digest.run_weekly")
def run_weekly_digest(profiles: list[dict] | None = None, force: bool = False,
                      dry_run: bool = False):
    """Entry point for the weekly cron (and manual runs). Fans out one task per segment."""
    week = digest_agent.week_id()
    if not force and not store.acquire_run_lock(week):
        return {"week": week, "skipped": "already ran this week (use force=true)"}

    plist = [Profile.from_dict(p) for p in profiles] if profiles else fetch_profiles()
    segments = segment_profiles(plist)
    store.init_run(week, len(segments))
    for seg in segments:
        store.save_segment_students(seg.segment_id, seg.student_ids)
        build_segment_digest.delay(asdict(seg), week, dry_run)
    return {"week": week, "students": len(plist), "segments": len(segments), "dry_run": dry_run}


@celery.task(bind=True, name="digest.build_segment", autoretry_for=RETRYABLE,
             retry_backoff=60, retry_backoff_max=600, max_retries=2, rate_limit="6/m")
def build_segment_digest(self, segment: dict, week: str, dry_run: bool = False):
    seg_id = segment["segment_id"]
    try:
        digest = digest_agent.build_digest(segment, week, store.get_seen(seg_id))

        if digest["status"] == "empty":
            store.save_digest(digest)
            store.bump(week, "empty")
            return {"segment": seg_id, "status": "empty"}

        if dry_run:
            digest["status"] = "dry_run"
        else:
            digest["status"] = "published" if settings.digest_auto_publish else "pending_review"
        store.save_digest(digest)

        if digest["status"] == "published":
            store.mark_seen(seg_id, [i["url"] for i in digest["items"]])
            digest["delivered"] = delivery.push_digest(digest, store.get_segment_students(seg_id))
            store.save_digest(digest)

        store.bump(week, "done")
        return {"segment": seg_id, "status": digest["status"], "items": len(digest["items"]),
                "delivered": digest["delivered"]}
    except Exception as e:
        if not isinstance(e, RETRYABLE) or self.request.retries >= self.max_retries:
            store.bump(week, "failed")
        raise


@celery.task(bind=True, name="digest.build_student", autoretry_for=RETRYABLE,
             retry_backoff=60, retry_backoff_max=600, max_retries=2, rate_limit="6/m")
def build_student_digest(self, segment: dict, week: str, student_id: str):
    segment_id = segment["segment_id"]
    try:
        digest = digest_agent.build_digest(segment, week, store.get_seen(segment_id))
        digest["status"] = "published" if digest["items"] else "empty"
        if digest["items"]:
            store.mark_seen(segment_id, [item["url"] for item in digest["items"]])
        store.save_digest(digest)
        store.save_segment_students(segment_id, [student_id])
        return {"segment": segment_id, "status": digest["status"], "items": len(digest["items"])}
    except Exception as error:
        if not isinstance(error, RETRYABLE) or self.request.retries >= self.max_retries:
            store.release_student_lock(week, segment_id)
        raise
    finally:
        if not self.request.called_directly or self.request.retries >= 0:
            store.release_student_lock(week, segment_id)
