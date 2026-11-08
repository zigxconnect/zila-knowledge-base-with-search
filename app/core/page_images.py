"""Find a representative image (and its alt text) on an opportunity's own web page.

It reads Open Graph / Twitter-card tags from the page <head>, the same way chat apps build
link previews, so the image belongs to *that* opportunity.

The page URLs come from web search results, so they are treated as untrusted (SSRF defence):
  * only http(s) on ports 80/443, and every redirect hop is checked again
  * the host must resolve to public addresses only (no localhost, private, link-local, reserved)
  * hard limits on time, redirects and bytes read; no cookies kept between pages
  * the image URL is checked too (must really be a raster image, not too large)
"""
import ipaddress
import logging
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from app.config import settings

log = logging.getLogger(__name__)

MAX_HTML_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3
OVERALL_SECONDS = 12.0
ALLOWED_PORTS = {None, 80, 443}
BAD_IMAGE_HINTS = ("favicon", "sprite", "apple-touch-icon", "blank.", "pixel.", "spacer.")
BAD_EXTENSIONS = (".svg", ".ico")
IMAGE_KEYS = ("og:image:secure_url", "og:image", "og:image:url",
              "twitter:image", "twitter:image:src")
ALT_KEYS = ("og:image:alt", "twitter:image:alt")


# ---------------- safety ----------------
def is_public_url(url: str) -> bool:
    """True only for http(s) URLs on ports 80/443 whose host resolves to public IPs."""
    try:
        p = urlparse(url)
        port = p.port
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.hostname or port not in ALLOWED_PORTS:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, port or (443 if p.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            return False
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        if not ip.is_global:
            return False
    return True


def _make_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(6.0),
        follow_redirects=False,                 # redirects are followed by hand, with checks
        headers={"User-Agent": settings.digest_fetch_user_agent,
                 "Accept-Language": "en,fr;q=0.8"},
    )


def _fetch(client: httpx.Client, url: str, accept: str, deadline: float,
           read_body: bool) -> dict | None:
    """GET with manual, validated redirects. Returns url/ctype/length/body or None."""
    for _ in range(MAX_REDIRECTS + 1):
        if time.monotonic() > deadline or not is_public_url(url):
            return None
        try:
            with client.stream("GET", url, headers={"Accept": accept}) as r:
                if r.is_redirect:
                    loc = r.headers.get("location")
                    if not loc:
                        return None
                    url = urljoin(url, loc)
                    continue
                if r.status_code not in (200, 206):
                    return None
                body = b""
                if read_body:
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf += chunk
                        if len(buf) >= MAX_HTML_BYTES:
                            break
                    body = bytes(buf)
                try:
                    length = int(r.headers.get("content-length", "-1"))
                except ValueError:
                    length = -1
                return {"url": url, "ctype": r.headers.get("content-type", "").lower(),
                        "length": length, "body": body}
        except httpx.HTTPError:
            return None
    return None


# ---------------- parsing ----------------
class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.image_src: str | None = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").strip().lower()
            content = a.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag == "link" and "image_src" in a.get("rel", "").lower().split() and a.get("href"):
            self.image_src = a["href"].strip()


def _charset(ctype: str) -> str:
    m = re.search(r"charset=([\w-]+)", ctype)
    enc = m.group(1) if m else "utf-8"
    try:
        "".encode(enc)
        return enc
    except LookupError:
        return "utf-8"


def _clean_alt(text: str | None) -> str | None:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:160] if text else None


def _plausible(image_url: str) -> bool:
    low = image_url.lower()
    path = urlparse(low).path
    return (low.startswith("https://") and len(low) < 2000
            and not path.endswith(BAD_EXTENSIONS)
            and not any(h in path for h in BAD_IMAGE_HINTS))


def _image_ok(client: httpx.Client, image_url: str, deadline: float) -> bool:
    res = _fetch(client, image_url, "image/*", deadline, read_body=False)
    if not res or not res["ctype"].startswith("image/") or "svg" in res["ctype"]:
        return False
    return res["length"] < 0 or res["length"] <= MAX_IMAGE_BYTES


# ---------------- public function ----------------
def find_page_image(page_url: str) -> dict | None:
    """Returns {'image_url': str, 'alt': str | None} or None when the page has no usable image."""
    if settings.mock_mode:
        return {"image_url": "https://placehold.co/600x300.jpg", "alt": "Sample event banner"}

    deadline = time.monotonic() + OVERALL_SECONDS
    with _make_client() as client:
        page = _fetch(client, page_url, "text/html,application/xhtml+xml", deadline, read_body=True)
        if not page or "html" not in page["ctype"]:
            return None
        parser = _MetaParser()
        try:
            parser.feed(page["body"].decode(_charset(page["ctype"]), errors="replace"))
        except Exception:                        # noqa: BLE001  (malformed HTML)
            return None

        candidates = [parser.meta[k] for k in IMAGE_KEYS if k in parser.meta]
        if parser.image_src:
            candidates.append(parser.image_src)
        alt = _clean_alt(next((parser.meta[k] for k in ALT_KEYS if k in parser.meta), None))

        for raw in candidates[:3]:
            image_url = urljoin(page["url"], raw)
            if _plausible(image_url) and _image_ok(client, image_url, deadline):
                return {"image_url": image_url, "alt": alt}
    return None
