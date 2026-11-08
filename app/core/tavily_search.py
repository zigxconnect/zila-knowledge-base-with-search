import time
from functools import lru_cache

from tavily import TavilyClient

from app.config import settings

_NO_RETRY = {"InvalidAPIKeyError", "MissingAPIKeyError", "UsageLimitExceededError", "BadRequestError"}


@lru_cache(maxsize=1)
def _client() -> TavilyClient:
    if not settings.tavily_api_key:
        raise RuntimeError("TAVILY_API_KEY is not set")
    return TavilyClient(api_key=settings.tavily_api_key)


def search_web(query: str, max_results: int = 6, country: str | None = None) -> dict:
    """Returns {'results': [{url,title,content,score,...}], 'images': [{url,description}]}.
    `country` boosts (does not restrict) results from that country."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            res = _client().search(
                query=query,
                topic="general",
                search_depth="basic",            # 1 credit; "advanced" costs more
                max_results=max_results,
                time_range="month",
                include_images=True,
                include_image_descriptions=True,
                **({"country": country.lower()} if country else {}),
            )
            images = []
            for im in res.get("images", []) or []:
                if isinstance(im, str):
                    images.append({"url": im, "description": ""})
                elif isinstance(im, dict) and im.get("url"):
                    images.append({"url": im["url"], "description": im.get("description") or ""})
            return {"results": res.get("results", []) or [], "images": images}
        except Exception as e:                  # noqa: BLE001
            if type(e).__name__ in _NO_RETRY:
                raise
            last = e
            time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]
