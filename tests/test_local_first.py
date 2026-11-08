"""Local first: the student's city, then Cameroon, then Africa, then online, then the rest of the world."""
from app.config import settings
from app.core import digest_agent, llm, tavily_search

SEG = {"segment_id": "student__x", "department": "computer engineering", "location": "Bamenda", "size": 1,
       "top_skills": ["python"], "top_interests": [], "levels": []}


def test_reach_from_location_text():
    r = lambda loc, model=None: digest_agent._reach(loc, model, "Bamenda", "Cameroon")
    assert r("Bamenda, North West") == "local"
    assert r("Douala, Cameroon") == "national"
    assert r("Yaoundé") == "national"                      # a Cameroonian city without the country name
    assert r("Kigali, Rwanda") == "africa"
    assert r("Online via Zoom") == "online"
    assert r("Seattle, USA") == "international"
    assert r(None, "online") == "online"                   # no location: the model's label is used
    assert r(None, "made-up") == "international"


def test_home_defaults_to_the_platform_country():
    assert digest_agent._home({"location": "Bamenda"}) == ("Bamenda", "Cameroon")
    assert digest_agent._home({"location": None}) == ("", "Cameroon")
    assert digest_agent._home({"location": "Remote"}) == ("", "Cameroon")


def _items(*reaches):
    return [{"title": f"{r}{i}", "reach": r} for i, r in enumerate(reaches)]


def test_closest_first_and_far_away_kept_only_when_short():
    out = digest_agent._order_by_reach(_items("international", "online", "local", "national", "africa"))
    assert [i["reach"] for i in out] == ["local", "national", "africa", "online", "international"]
    full = digest_agent._order_by_reach(_items(*["local"] * settings.digest_max_items, "international"))
    assert all(i["reach"] == "local" for i in full)        # enough nearby: nothing far away
    short = digest_agent._order_by_reach(_items("local", "international", "international", "international"))
    assert [i["reach"] for i in short] == ["local", "international", "international"]   # topped up to MIN_ITEMS


def test_video_pages_never_get_an_image(monkeypatch):
    monkeypatch.setattr(settings, "digest_fetch_page_images", False)
    items = [{"url": "https://www.facebook.com/123/videos/follow-hack/", "domain": "facebook.com", "title": "T",
              "image_url": "https://x.org/still.jpg", "image_caption": "c", "image_credit": "x.org",
              "image_source": "search"}]
    digest_agent._enrich_with_page_images(items)
    assert items[0]["image_url"] is None and items[0]["image_source"] is None


def test_digest_orders_local_first_and_boosts_home_country(monkeypatch):
    calls = []

    def fake_search(q, max_results=6, country=None):
        calls.append(country)
        return {"results": [{"url": f"https://s{i}.org/x", "title": f"T{i}", "content": "c", "score": 1}
                            for i in range(4)], "images": []}

    def fake_llm(prompt, **kw):
        if prompt.startswith("TASK: PLAN_QUERIES"):
            assert "Bamenda" in prompt and "Cameroon" in prompt
            return {"queries": ["computer engineering internships Bamenda"]}
        assert "Local first" in prompt
        locs = ["Seattle, USA", "Online", "Bamenda", "Douala, Cameroon"]
        return {"headline": "H", "intro": "I", "items": [
            {"source_id": i, "title": f"T{i}", "summary": "s", "why_relevant": "w", "location": locs[i]}
            for i in range(4)]}

    monkeypatch.setattr(tavily_search, "search_web", fake_search)
    monkeypatch.setattr(llm, "generate_json", fake_llm)
    d = digest_agent.build_digest(SEG, "2026-W45", set())
    assert calls and all(c == "Cameroon" for c in calls)
    assert [i["reach"] for i in d["items"]] == ["local", "national", "online", "international"]
