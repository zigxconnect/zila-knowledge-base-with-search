import json

from app.core.status import r

DIGEST_TTL = 60 * 60 * 24 * 120
SEEN_TTL = 60 * 60 * 24 * 60
MAP_TTL = 60 * 60 * 24 * 30


def acquire_run_lock(week: str) -> bool:
    return bool(r.set(f"digest_run_lock:{week}", "1", nx=True, ex=6 * 86400))


def acquire_student_lock(week: str, segment_id: str) -> bool:
    return bool(r.set(f"digest_student_lock:{week}:{segment_id}", "1", nx=True, ex=5 * 60))


def release_student_lock(week: str, segment_id: str):
    r.delete(f"digest_student_lock:{week}:{segment_id}")


def init_run(week: str, total: int):
    key = f"digest_run:{week}"
    r.delete(key)
    r.hset(key, mapping={"total": total, "done": 0, "empty": 0, "failed": 0})
    r.expire(key, DIGEST_TTL)


def bump(week: str, field: str):
    r.hincrby(f"digest_run:{week}", field, 1)


def run_status(week: str) -> dict | None:
    return r.hgetall(f"digest_run:{week}") or None


def save_digest(d: dict):
    seg, week = d["segment_id"], d["week"]
    r.set(f"digest:{week}:{seg}", json.dumps(d, ensure_ascii=False), ex=DIGEST_TTL)
    r.sadd(f"digests:{week}", seg)
    r.expire(f"digests:{week}", DIGEST_TTL)
    r.set(f"digest_latest:{seg}", week, ex=DIGEST_TTL)


def get_digest(week: str, segment_id: str) -> dict | None:
    raw = r.get(f"digest:{week}:{segment_id}")
    return json.loads(raw) if raw else None


def list_digests(week: str) -> list[dict]:
    out = []
    for seg in sorted(r.smembers(f"digests:{week}")):
        d = get_digest(week, seg)
        if d:
            out.append({"segment_id": seg, "headline": d["headline"], "status": d["status"],
                        "items": len(d["items"]), "delivered": d.get("delivered", False)})
    return out


def latest_week(segment_id: str) -> str | None:
    return r.get(f"digest_latest:{segment_id}")


def get_seen(segment_id: str) -> set[str]:
    return set(r.smembers(f"seen:{segment_id}"))


def mark_seen(segment_id: str, urls: list[str]):
    if urls:
        r.sadd(f"seen:{segment_id}", *urls)
        r.expire(f"seen:{segment_id}", SEEN_TTL)


def save_segment_students(segment_id: str, student_ids: list[str]):
    key = f"segment_students:{segment_id}"
    pipe = r.pipeline()
    pipe.delete(key)
    for i in range(0, len(student_ids), 500):
        pipe.sadd(key, *student_ids[i:i + 500])
    pipe.expire(key, MAP_TTL)
    for sid in student_ids:
        pipe.set(f"student_segment:{sid}", segment_id, ex=MAP_TTL)
    pipe.execute()


def get_segment_students(segment_id: str) -> list[str]:
    return sorted(r.smembers(f"segment_students:{segment_id}"))


def student_segment(student_id: str) -> str | None:
    return r.get(f"student_segment:{student_id}")
