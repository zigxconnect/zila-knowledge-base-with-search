"""Student profiles -> segments. Segments, not individuals, drive the web search."""
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

import httpx

from app.config import settings

MIN_SEGMENT_SIZE = 5


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "any"


def _as_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def _combined_lists(d: dict, *keys: str) -> list[str]:
    values = []
    seen = set()
    for key in keys:
        for value in _as_list(d.get(key)):
            normalized = value.casefold()
            if normalized not in seen:
                values.append(value)
                seen.add(normalized)
    return values


@dataclass
class Profile:
    id: str
    department: str
    skills: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    level: str | None = None
    location: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        """Map Zigex student profile fields into the digest's normalized shape."""
        nested = d.get("profile")
        source = {**d, **nested} if isinstance(nested, dict) else d
        return cls(
            id=str(source.get("id") or source.get("student_id") or d.get("id") or d.get("student_id")),
            department=_norm(
                source.get("department")
                or source.get("track")
                or source.get("field")
                or source.get("field_of_study")
                or source.get("fieldOfStudy")
            ),
            skills=_combined_lists(source, "skills", "hard_skills", "hardSkills", "soft_skills", "softSkills"),
            interests=_combined_lists(source, "interests", "preferred_industries", "preferredIndustries"),
            level=source.get("level") or source.get("education_level") or source.get("educationLevel") or source.get("degree"),
            location=source.get("location") or source.get("city") or source.get("country"),
        )


@dataclass
class Segment:
    segment_id: str
    department: str
    location: str | None
    size: int
    top_skills: list[str]
    top_interests: list[str]
    levels: list[str]
    student_ids: list[str]

    def public(self) -> dict:
        """What the LLM / Tavily may see: aggregate attributes only, no student IDs."""
        d = asdict(self)
        d.pop("student_ids")
        return d


def _build(dept: str, loc: str | None, members: list[Profile]) -> Segment:
    skills = Counter(s.lower() for m in members for s in m.skills)
    interests = Counter(s.lower() for m in members for s in m.interests)
    levels = Counter(_norm(m.level) for m in members if m.level)
    return Segment(
        segment_id=f"{_slug(dept)}__{_slug(loc or 'any')}",
        department=dept,
        location=loc,
        size=len(members),
        top_skills=[k for k, _ in skills.most_common(8)],
        top_interests=[k for k, _ in interests.most_common(6)],
        levels=[k for k, _ in levels.most_common(3)],
        student_ids=[m.id for m in members],
    )


def segment_profiles(profiles: list[Profile]) -> list[Segment]:
    """Group by (department, location); tiny groups fall back to department-wide."""
    groups: dict[tuple[str, str | None], list[Profile]] = {}
    for p in profiles:
        if not p.department:
            continue
        loc = _norm(p.location) or None
        groups.setdefault((p.department, loc), []).append(p)

    merged: dict[tuple[str, str | None], list[Profile]] = {}
    for (dept, loc), members in groups.items():
        key = (dept, loc) if len(members) >= MIN_SEGMENT_SIZE else (dept, None)
        merged.setdefault(key, []).extend(members)

    return [_build(d, l, m) for (d, l), m in merged.items()]


def personalized_segment(profile: Profile) -> Segment | None:
    department = _norm(profile.department)
    if not department:
        return None

    normalized = json.dumps(
        {
            "student_id": profile.id,
            "department": department,
            "skills": sorted(skill.casefold() for skill in profile.skills),
            "interests": sorted(interest.casefold() for interest in profile.interests),
            "level": _norm(profile.level),
            "location": _norm(profile.location),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    segment = _build(department, _norm(profile.location) or None, [profile])
    segment.segment_id = f"student__{hashlib.sha256(normalized.encode()).hexdigest()[:24]}"
    return segment


def fetch_profiles() -> list[Profile]:
    """Pulls every student from the Zigex API (expects {'results': [...], 'next': ...} or a list)."""
    if not settings.zigex_api_url:
        raise RuntimeError("ZIGEX_API_URL is not set. Pass `profiles` in the run request instead.")
    headers = {}
    if settings.zigex_api_token:
        headers["Authorization"] = f"Bearer {settings.zigex_api_token}"
    out: list[Profile] = []
    with httpx.Client(timeout=30, headers=headers) as c:
        for page in range(1, 101):
            r = c.get(settings.zigex_api_url, params={"page": page, "page_size": 200})
            r.raise_for_status()
            data = r.json()
            rows = data.get("results", []) if isinstance(data, dict) else data
            if not rows:
                break
            out.extend(Profile.from_dict(x) for x in rows)
            if isinstance(data, dict) and not data.get("next"):
                break
            if isinstance(data, list) and len(rows) < 200:
                break
    return out
