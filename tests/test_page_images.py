import httpx
import pytest

from app.config import settings
from app.core import digest_agent, llm, page_images, tavily_search

PAGE = "https://events.test/hack"


# ---------------- SSRF guard ----------------
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://localhost/",
    "http://10.0.0.5/",
    "http://192.168.1.10/",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata address
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://qdrant:6333/collections",             # internal service, blocked port and name
    "http://redis:6379/",
    "http://8.8.8.8:8443/",                       # public IP but unusual port
    "ftp://8.8.8.8/file",
    "file:///etc/passwd",
    "not a url",
])
def test_internal_and_odd_urls_are_blocked(url):
    assert page_images.is_public_url(url) is False


def test_public_ip_is_allowed():
    assert page_images.is_public_url("https://8.8.8.8/") is True


# ---------------- helpers for fake web ----------------
def fake_web(monkeypatch, routes, seen=None):
    """routes: {url: (status, headers, content)}. Every URL is treated as public unless it
    points at 127.0.0.1."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        status, headers, content = routes[str(request.url)]
        return httpx.Response(status, headers=headers, content=content)

    monkeypatch.setattr(page_images, "_make_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler),
                                             follow_redirects=False))
    monkeypatch.setattr(page_images, "is_public_url", lambda u: "127.0.0.1" not in u)


def html(head: str) -> tuple:
    return (200, {"content-type": "text/html; charset=utf-8"},
            f"<html><head>{head}</head><body>x</body></html>".encode())


JPEG = (200, {"content-type": "image/jpeg", "content-length": "1000"}, b"x")


# ---------------- extraction ----------------
def test_reads_og_image_resolves_relative_url_and_alt(monkeypatch):
    fake_web(monkeypatch, {
        PAGE: html('<meta property="og:image" content="/img/banner.jpg">'
                   '<meta property="og:image:alt" content="  Participants at   the hackathon ">'),
        "https://events.test/img/banner.jpg": JPEG,
    })
    assert page_images.find_page_image(PAGE) == {
        "image_url": "https://events.test/img/banner.jpg",
        "alt": "Participants at the hackathon"}


def test_falls_back_to_twitter_image(monkeypatch):
    fake_web(monkeypatch, {
        PAGE: html('<meta name="twitter:image" content="https://cdn.test/t.png">'),
        "https://cdn.test/t.png": (200, {"content-type": "image/png"}, b"x"),
    })
    assert page_images.find_page_image(PAGE)["image_url"] == "https://cdn.test/t.png"


def test_page_without_image_returns_none(monkeypatch):
    fake_web(monkeypatch, {PAGE: html('<title>No image here</title>')})
    assert page_images.find_page_image(PAGE) is None


@pytest.mark.parametrize("image_response", [
    (200, {"content-type": "text/html"}, b"<html>login</html>"),               # not an image
    (200, {"content-type": "image/svg+xml"}, b"<svg/>"),                        # svg rejected
    (200, {"content-type": "image/jpeg", "content-length": "99999999"}, b"x"),  # too large
    (404, {}, b""),                                                             # broken link
])
def test_bad_images_are_rejected(monkeypatch, image_response):
    fake_web(monkeypatch, {
        PAGE: html('<meta property="og:image" content="https://cdn.test/a.jpg">'),
        "https://cdn.test/a.jpg": image_response,
    })
    assert page_images.find_page_image(PAGE) is None


def test_logos_http_and_favicons_are_skipped(monkeypatch):
    fake_web(monkeypatch, {
        PAGE: html('<meta property="og:image" content="http://cdn.test/a.jpg">'
                   '<meta name="twitter:image" content="https://cdn.test/favicon.png">'),
    })
    assert page_images.find_page_image(PAGE) is None       # http and favicon both rejected


def test_non_html_page_is_ignored(monkeypatch):
    fake_web(monkeypatch, {PAGE: (200, {"content-type": "application/pdf"}, b"%PDF")})
    assert page_images.find_page_image(PAGE) is None


# ---------------- redirects ----------------
def test_redirect_to_internal_address_is_never_followed(monkeypatch):
    seen = []
    fake_web(monkeypatch, {
        PAGE: (302, {"location": "http://127.0.0.1/admin"}, b""),
        "http://127.0.0.1/admin": html('<meta property="og:image" content="https://x.test/a.jpg">'),
    }, seen)
    assert page_images.find_page_image(PAGE) is None
    assert seen == [PAGE]                                   # the internal URL was never requested


def test_safe_redirect_is_followed(monkeypatch):
    fake_web(monkeypatch, {
        PAGE: (301, {"location": "https://events.test/final"}, b""),
        "https://events.test/final": html('<meta property="og:image" content="/b.jpg">'),
        "https://events.test/b.jpg": JPEG,
    })
    assert page_images.find_page_image(PAGE)["image_url"] == "https://events.test/b.jpg"


def test_redirect_loop_gives_up(monkeypatch):
    fake_web(monkeypatch, {PAGE: (302, {"location": PAGE}, b"")})
    assert page_images.find_page_image(PAGE) is None


# ---------------- integration with the agent ----------------
def _items():
    return [
        {"title": "A", "url": "https://a.org/x", "domain": "a.org",
         "image_url": "https://search.img/a.jpg", "image_caption": "from search",
         "image_credit": "search.img", "image_source": "search"},
        {"title": "B", "url": "https://b.org/x", "domain": "b.org",
         "image_url": "https://search.img/b.jpg", "image_caption": "kept",
         "image_credit": "search.img", "image_source": "search"},
        {"title": "C", "url": "https://c.org/x", "domain": "c.org",
         "image_url": None, "image_caption": None, "image_credit": None, "image_source": None},
    ]


def test_page_image_replaces_search_image_and_failures_keep_fallback(monkeypatch):
    monkeypatch.setattr(settings, "digest_fetch_page_images", True)

    def fake_find(url):
        if url == "https://a.org/x":
            return {"image_url": "https://a.org/og.jpg", "alt": "Hackathon crowd"}
        if url == "https://c.org/x":
            raise RuntimeError("boom")                      # must not break the digest
        return None

    monkeypatch.setattr(page_images, "find_page_image", fake_find)
    a, b, c = _items()
    digest_agent._enrich_with_page_images([a, b, c])

    assert (a["image_url"], a["image_caption"], a["image_credit"], a["image_source"]) == \
        ("https://a.org/og.jpg", "Hackathon crowd", "a.org", "page")
    assert (b["image_url"], b["image_source"]) == ("https://search.img/b.jpg", "search")
    assert (c["image_url"], c["image_source"]) == (None, None)


def test_caption_falls_back_to_title_when_page_has_no_alt(monkeypatch):
    monkeypatch.setattr(settings, "digest_fetch_page_images", True)
    monkeypatch.setattr(page_images, "find_page_image",
                        lambda url: {"image_url": "https://a.org/og.jpg", "alt": None})
    a = _items()[0]
    digest_agent._enrich_with_page_images([a])
    assert a["image_caption"] == "A"


def test_flag_off_means_no_fetching(monkeypatch):
    def explode(url):
        raise AssertionError("must not fetch")

    monkeypatch.setattr(page_images, "find_page_image", explode)
    digest_agent._enrich_with_page_images(_items())          # flag is off via conftest


def test_markdown_escapes_awkward_captions_and_urls():
    item = {"title": "T", "summary": "s", "why_relevant": "w", "category": "other",
            "event_date": None, "deadline": None, "location": None,
            "url": "https://a.org/x", "domain": "a.org",
            "image_url": "https://a.org/my image (1).jpg",
            "image_caption": "Line one]\nLine [two]", "image_credit": "a.org"}
    md = digest_agent.render_markdown("H", "I", [item], "2026-W42")
    assert "![Line one) Line (two)](https://a.org/my%20image%20%281%29.jpg)" in md


def test_full_digest_uses_page_images(monkeypatch):
    monkeypatch.setattr(settings, "digest_fetch_page_images", True)
    monkeypatch.setattr(tavily_search, "search_web", lambda q, max_results=6, **kw: {
        "results": [{"url": f"https://s{i}.org/x", "title": f"T{i}", "content": "c", "score": 1}
                    for i in range(3)], "images": []})

    def fake_llm(prompt, **kw):
        if prompt.startswith("TASK: PLAN_QUERIES"):
            return {"queries": ["a b c"]}
        return {"headline": "H", "intro": "I", "items": [
            {"source_id": i, "title": f"T{i}", "summary": "s", "why_relevant": "w"}
            for i in range(3)]}

    monkeypatch.setattr(llm, "generate_json", fake_llm)
    monkeypatch.setattr(page_images, "find_page_image", lambda url: {
        "image_url": url.replace("/x", "/og.jpg"), "alt": "Banner"})

    seg = {"segment_id": "ml__x", "department": "ml", "location": "x", "size": 6,
           "top_skills": [], "top_interests": [], "levels": []}
    d = digest_agent.build_digest(seg, "2026-W42", set())
    assert [i["image_source"] for i in d["items"]] == ["page"] * 3
    assert "![Banner](https://s0.org/og.jpg)" in d["markdown"]
