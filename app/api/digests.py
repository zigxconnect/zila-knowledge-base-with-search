from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import delivery, digest_agent, digest_store as store
from dataclasses import asdict

from app.core.profiles import Profile, _slug, personalized_segment
from app.security import require_api_key
from app.worker.digest_tasks import build_student_digest, run_weekly_digest

router = APIRouter(prefix="/v1/digests", dependencies=[Depends(require_api_key)])


class ProfileIn(BaseModel):
    id: str
    department: str
    skills: list[str] = []
    interests: list[str] = []
    level: str | None = None
    location: str | None = None


class RunRequest(BaseModel):
    force: bool = False
    dry_run: bool = False
    profiles: list[ProfileIn] | None = Field(
        None, description="Optional override. Omit to pull students from the Zigex API.")


class PreviewRequest(BaseModel):
    department: str
    location: str | None = None
    skills: list[str] = []
    interests: list[str] = []
    level: str | None = None


class StudentRecommendationRequest(BaseModel):
    department: str = Field(min_length=1, max_length=120)
    skills: list[str] = Field(default_factory=list, max_length=40)
    interests: list[str] = Field(default_factory=list, max_length=30)
    level: str | None = Field(default=None, max_length=80)
    location: str | None = Field(default=None, max_length=120)


@router.post("/run", status_code=202)
def run(req: RunRequest):
    """Queue a digest run (same job the weekly cron triggers)."""
    task = run_weekly_digest.delay(
        profiles=[p.model_dump() for p in req.profiles] if req.profiles else None,
        force=req.force, dry_run=req.dry_run)
    return {"queued": True, "task_id": task.id, "week": digest_agent.week_id()}


@router.post("/preview")
def preview(req: PreviewRequest):
    """Synchronous single-segment test. Nothing is saved or sent. Can take 30-90 s."""
    seg = {
        "segment_id": f"preview__{_slug(req.department)}__{_slug(req.location or 'any')}",
        "department": req.department.lower(), "location": req.location, "size": 1,
        "top_skills": req.skills, "top_interests": req.interests,
        "levels": [req.level] if req.level else [],
    }
    try:
        return digest_agent.build_digest(seg, seen_urls=set())
    except Exception as e:                      # noqa: BLE001
        raise HTTPException(502, f"Digest generation failed: {e}")


@router.post("/student/{student_id}/recommendation", status_code=202)
def student_recommendation(student_id: str, req: StudentRecommendationRequest):
    profile = Profile(
        id=student_id,
        department=req.department.strip().lower(),
        skills=req.skills,
        interests=req.interests,
        level=req.level,
        location=req.location,
    )
    segment = personalized_segment(profile)
    if not segment:
        raise HTTPException(422, "A study field or department is required")

    week = digest_agent.week_id()
    cached = store.get_digest(week, segment.segment_id)
    if cached:
        if cached["status"] == "published":
            store.save_segment_students(segment.segment_id, [student_id])
            return {"status": "ready", "digest": cached}
        if cached["status"] == "empty":
            return {"status": "empty"}

    if not store.acquire_student_lock(week, segment.segment_id):
        return {"status": "queued", "week": week}

    try:
        task = build_student_digest.delay(asdict(segment), week, student_id)
    except Exception as error:
        store.release_student_lock(week, segment.segment_id)
        raise HTTPException(503, "Could not queue recommendations") from error
    return {"status": "queued", "week": week, "task_id": task.id}


@router.get("/run-status")
def run_status(week: str | None = None):
    week = week or digest_agent.week_id()
    s = store.run_status(week)
    if not s:
        raise HTTPException(404, f"No run recorded for {week}")
    return {"week": week, **s}


@router.get("")
def list_digests(week: str | None = None):
    week = week or digest_agent.week_id()
    return {"week": week, "digests": store.list_digests(week)}


@router.get("/student/{student_id}")
def digest_for_student(student_id: str):
    """Latest published digest for a student's segment (for the Zigex frontend)."""
    seg = store.student_segment(student_id)
    week = store.latest_week(seg) if seg else None
    d = store.get_digest(week, seg) if week else None
    if not d or d["status"] != "published":
        raise HTTPException(404, "No published digest for this student yet")
    return d


@router.get("/{week}/{segment_id}")
def get_digest(week: str, segment_id: str):
    d = store.get_digest(week, segment_id)
    if not d:
        raise HTTPException(404, "Not found")
    return d


@router.post("/{week}/{segment_id}/publish")
def publish(week: str, segment_id: str):
    """Approve a pending_review digest (or re-send a failed delivery)."""
    d = store.get_digest(week, segment_id)
    if not d or not d["items"]:
        raise HTTPException(404, "Not found or empty")
    d["status"] = "published"
    store.mark_seen(segment_id, [i["url"] for i in d["items"]])
    d["delivered"] = delivery.push_digest(d, store.get_segment_students(segment_id))
    store.save_digest(d)
    return {"segment_id": segment_id, "status": d["status"], "delivered": d["delivered"]}
