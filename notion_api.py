"""Small Notion REST client: paginated queries, page creation and updates.

Requests are spaced to stay under Notion's rate limit (~3 requests/second)
and retried with backoff when Notion asks us to slow down.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

import requests

API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-02-22"  # the API version this database has been synced with
# Responses that mean "not processed, try again". Safe to retry even for page creation.
RETRY_ALWAYS = {409, 429, 503}
# Ambiguous server errors: only retried for reads and updates, never for creation,
# so a timed-out create can't turn into a duplicate page.
RETRY_IDEMPOTENT = {500, 502, 504}


class NotionError(RuntimeError):
    def __init__(self, status: Optional[int], message: str):
        self.status = status
        super().__init__(f"HTTP {status}: {message}" if status else message)


class NotionClient:
    def __init__(
        self,
        token: str,
        version: str = NOTION_VERSION,
        session: Optional[requests.Session] = None,
        min_interval: float = 0.34,
        max_retries: int = 5,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Notion-Version": version,
                "Content-Type": "application/json",
            }
        )
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._last_request = float("-inf")

    def _throttle(self) -> None:
        wait = self._last_request + self.min_interval - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last_request = self._clock()

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, 1.0 * 2**attempt)

    def request(self, method: str, path: str, body: Optional[dict] = None, idempotent: bool = True) -> Dict[str, Any]:
        retryable = RETRY_ALWAYS | (RETRY_IDEMPOTENT if idempotent else set())
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                res = self.session.request(method, API_URL + path, json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                if not idempotent or attempt == self.max_retries:
                    raise NotionError(None, f"{type(exc).__name__}: {exc}") from exc
                self._sleep(self._backoff(attempt))
                continue
            if res.status_code < 300:
                return res.json()
            if res.status_code in retryable and attempt < self.max_retries:
                retry_after = res.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else self._backoff(attempt)
                except ValueError:
                    wait = self._backoff(attempt)
                self._sleep(wait)
                continue
            raise NotionError(res.status_code, _error_message(res))
        raise NotionError(None, "retries exhausted")  # pragma: no cover

    def query_database(self, database_id: str) -> List[Dict[str, Any]]:
        """Every page in the database, following pagination."""
        pages: List[Dict[str, Any]] = []
        cursor = None
        while True:
            body: Dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            data = self.request("POST", f"/databases/{database_id}/query", body)
            pages.extend(data.get("results", []))
            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor:
                return pages

    def create_page(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("POST", "/pages", payload, idempotent=False)

    def update_page(self, page_id: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("PATCH", f"/pages/{page_id}", {"properties": properties})

    def archive_page(self, page_id: str) -> Dict[str, Any]:
        """Move a page to Notion's trash (restorable from there)."""
        return self.request("PATCH", f"/pages/{page_id}", {"archived": True})


def _error_message(res: requests.Response) -> str:
    try:
        data = res.json()
        return f"{data.get('code', '')}: {data.get('message', '')}".strip(": ")
    except ValueError:
        return res.text[:300]


def plain_text(prop: Optional[Dict[str, Any]]) -> str:
    """The plain text of a title or rich_text property value (all segments)."""
    if not prop:
        return ""
    items = prop.get(prop.get("type") or "") or prop.get("title") or prop.get("rich_text") or []
    if not isinstance(items, list):
        return ""
    return "".join(
        item.get("plain_text") or (item.get("text") or {}).get("content") or "" for item in items
    )


def text_value(content: str, limit: int = 2000) -> List[Dict[str, Any]]:
    """A rich-text array holding ``content`` (Notion caps one item at 2000 chars)."""
    content = (content or "")[:limit]
    return [{"type": "text", "text": {"content": content}}] if content else []
