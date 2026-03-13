"""
Brave Web Search Tool — gives agents access to live web intelligence.
Uses the Brave Search API for real-time web results.
"""
import os
import urllib.request
import urllib.parse
import json

import logging

log = logging.getLogger(__name__)

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def brave_search(query: str, count: int = 5) -> list[dict]:
    """
    Search the web via Brave Search API.
    Returns list of {title, url, description} dicts.
    Non-fatal: returns empty list on failure.
    """
    if not BRAVE_API_KEY:
        log.warning("brave_search.no_api_key")
        return []

    try:
        params = urllib.parse.urlencode({"q": query, "count": count})
        url = f"{BRAVE_SEARCH_URL}?{params}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": BRAVE_API_KEY,
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())

        results = []
        for item in data.get("web", {}).get("results", [])[:count]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "description": item.get("description", ""),
            })

        log.info("brave_search.success", query=query, results=len(results))
        return results
    except Exception as e:
        log.warning("brave_search.error", query=query, error=str(e))
        return []


def search_summary(query: str, count: int = 5) -> str:
    """
    Search and return a formatted text summary.
    Ready to inject into agent prompts as context.
    """
    results = brave_search(query, count)
    if not results:
        return f"[No web results found for: {query}]"

    lines = [f"Web search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   {r['url']}")
        lines.append(f"   {r['description']}\n")

    return "\n".join(lines)
