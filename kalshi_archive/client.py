"""Rate-limited client for Kalshi's public trade API.

Anonymous by default (all endpoints used are public reads). If
KALSHI_ACCESS_KEY_ID and a private key are provided, requests are signed,
which buys the documented token budget (Basic tier: 200 tokens/s = 20 req/s
at the default 10 tokens/request; the self-serve Advanced tier: 30 req/s).
"""

import base64
import logging
import os
import random
import threading
import time

import requests

log = logging.getLogger("kalshi")

BASE_URL = "https://api.elections.kalshi.com"
API_PREFIX = "/trade-api/v2"


def _load_signer():
    """Return a sign(method, path) -> headers function, or None if no key configured."""
    key_id = os.environ.get("KALSHI_ACCESS_KEY_ID")
    pem_b64 = os.environ.get("KALSHI_PRIVATE_KEY_PEM_B64")
    pem_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if not key_id or not (pem_b64 or pem_path):
        return None
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pem = base64.b64decode(pem_b64) if pem_b64 else open(pem_path, "rb").read()
    private_key = serialization.load_pem_private_key(pem, password=None)

    def sign(method: str, path: str) -> dict:
        ts = str(int(time.time() * 1000))
        sig = private_key.sign(
            (ts + method + path).encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    return sign


class KalshiClient:
    def __init__(self, rps: float = 20.0):
        self._min_interval = 1.0 / rps
        self._lock = threading.Lock()
        self._next_ok = 0.0
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "kalshi-archive/1.0"
        self._sign = _load_signer()
        if self._sign:
            log.info("using signed requests (key %s...)", os.environ["KALSHI_ACCESS_KEY_ID"][:8])
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
        full_path = API_PREFIX + path
        last_err = None
        for attempt in range(10):
            self._throttle()
            headers = self._sign("GET", full_path) if self._sign else None
            try:
                resp = self._session.get(BASE_URL + full_path, params=params,
                                         headers=headers, timeout=60)
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
