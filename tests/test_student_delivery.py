"""A student's own recommendations are announced to Zigex once they're ready (push + email there)."""
import fakeredis

from app.core import delivery, digest_agent, digest_store
from app.worker import digest_tasks


def _run(monkeypatch, items):
    monkeypatch.setattr(digest_store, "r", fakeredis.FakeRedis(decode_responses=True))
    sent = []
    monkeypatch.setattr(delivery, "push_digest", lambda d, ids: sent.append((d["segment_id"], ids)) or True)
    monkeypatch.setattr(digest_agent, "build_digest", lambda seg, week, seen: {
        "segment_id": seg["segment_id"], "week": week, "headline": "H", "items": items, "status": "ok",
        "delivered": False})
    seg = {"segment_id": "student__abc", "department": "ml", "location": "Bamenda"}
    digest_tasks.build_student_digest.run(seg, "2026-W45", "profile-1")
    return sent, digest_store.get_digest("2026-W45", "student__abc")


def test_ready_recommendations_are_sent_to_zigex(monkeypatch):
    sent, saved = _run(monkeypatch, [{"url": "https://a.org/x", "title": "T"}])
    assert sent == [("student__abc", ["profile-1"])]
    assert saved["status"] == "published" and saved["delivered"] is True


def test_empty_recommendations_are_not_announced(monkeypatch):
    sent, saved = _run(monkeypatch, [])
    assert sent == [] and saved["status"] == "empty"
