"""Jina search service."""

import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from loguru import logger


_JINA_SEARCH_URL = "https://s.jina.ai/"
_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "entari-plugin-hyw/6.0",
    "X-Respond-With": "no-content",
}


def _clean_markdown_line(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    clean = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", clean)
    clean = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", clean)
    clean = re.sub(r"[*_`#>]+", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()


def _looks_like_linkish_stub(text: str) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return True
    if clean.lower() == "duckduckgo":
        return True
    if clean.startswith(("http://", "https://")):
        return True
    return " " not in clean and ("/" in clean or "." in clean)


def _extract_markdown_snippet(text: str, *, title: str) -> str:
    parts: List[str] = []
    clean_title = str(title or "").strip()
    for raw_line in str(text or "").splitlines():
        clean = _clean_markdown_line(raw_line)
        if not clean:
            continue
        if clean_title and clean == clean_title:
            continue
        if _looks_like_linkish_stub(clean):
            continue
        parts.append(clean)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()[:500]


class JinaSearchService:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 30.0):
        self._api_key = (api_key or os.environ.get("JINA_API_KEY") or "").strip()
        self._timeout = timeout

    def _build_search_url(self, query: str) -> str:
        clean_query = str(query or "").strip()
        return f"{_JINA_SEARCH_URL}{quote(clean_query)}"

    def _headers(self) -> Dict[str, str]:
        headers = dict(_DEFAULT_HEADERS)
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        if not str(query or "").strip():
            return []

        target_url = self._build_search_url(query)
        logger.info(f"JinaSearchService: query='{query}'")

        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(target_url, headers=self._headers())
            response.raise_for_status()
            return self._parse_response(response.json(), max_results=max_results)

    def _parse_response(self, payload: Dict[str, Any], *, max_results: int) -> List[Dict[str, Any]]:
        data = payload.get("data", [])
        if not isinstance(data, list):
            return []

        rows: List[Dict[str, Any]] = []
        seen: set[str] = set()

        for item in data:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            title = str(item.get("title") or "").strip() or "No Title"
            snippet = str(item.get("description") or "").strip()
            if not snippet:
                snippet = _extract_markdown_snippet(str(item.get("content") or ""), title=title)
            rows.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": re.sub(r"\s+", " ", snippet).strip()[:500],
                    "provider": "jina_search",
                }
            )
            if len(rows) >= max_results:
                break
        return rows
