"""Rate-limited client for Kalshi's public trade API (no auth needed for reads)."""

import logging
import random
import threading
import time

import requests

log = logging.getLogger("kalshi")

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    def __init__(self, rps: float = 8.0):
        self._min_interval = 1.0 / rps
        self._lock = threading.Lock()
        self._next_ok = 0.0
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "kalshi-archive/1.0"
        self.requests_made = 0

    def _throttle(self):
        with self._lock:
            now = time.monotonic()
            wait = self._next_ok - now
            self._next_ok = max(now, self._next_ok) + self._min_interval
        if wait > 0:
            time.sleep(wait)

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET with rate limiting and retries. Raises after 10 failed attempts."""
        last_err = None
        for attempt in range(10):
            self._throttle()
            try:
                resp = self._session.get(BASE_URL + path, params=params, timeout=60)
                self.requests_made += 1
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    raise NotFoundError(path)
                if resp.status_code == 429 or resp.status_code >= 500:
                    retry_after = float(resp.headers.get("Retry-After") or 0)
                    delay = max(retry_after, min(2**attempt, 60)) + random.random()
                    log.warning("HTTP %s on %s, retrying in %.1fs", resp.status_code, path, delay)
                    time.sleep(delay)
                    last_err = RuntimeError(f"HTTP {resp.status_code} on {path}: {resp.text[:200]}")
                    continue
                raise RuntimeError(f"HTTP {resp.status_code} on {path}: {resp.text[:500]}")
            except (requests.ConnectionError, requests.Timeout) as e:
                delay = min(2**attempt, 60) + random.random()
                log.warning("%s on %s, retrying in %.1fs", type(e).__name__, path, delay)
                time.sleep(delay)
                last_err = e
        raise RuntimeError(f"giving up on {path}") from last_err

    def paginate(self, path: str, params: dict, items_key: str, cursor: str | None = None):
        """Yield (page_items, cursor_after_page) until the API returns an empty cursor."""
        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            data = self.get(path, page_params)
            items = data.get(items_key) or []
            cursor = data.get("cursor") or ""
            yield items, cursor
            if not cursor:
                return


class NotFoundError(Exception):
    pass
