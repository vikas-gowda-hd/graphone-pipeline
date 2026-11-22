"""Shared async HTTP helpers.

- fetch(): GET with retries, exponential backoff + jitter, and Retry-After / GitHub
  rate-limit-header support. Reused later by the news/jobs crawlers and LLM clients.
- MinIntervalLimiter: enforces a minimum gap between requests (arXiv asks for 1 req / 3 s).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Optional

import aiohttp

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url} {body}".strip())
        self.status = status
        self.url = url
        self.body = body


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """Exponential backoff with 'equal jitter': half fixed, half random, capped."""
    ceiling = min(cap, base * (2 ** attempt))
    return ceiling / 2 + random.uniform(0, ceiling / 2)


def _server_delay(resp: aiohttp.ClientResponse) -> Optional[float]:
    """Delay the server explicitly asked for (Retry-After, or GitHub's rate-limit reset)."""
    retry_after = resp.headers.get("Retry-After")
    if retry_after and retry_after.strip().isdigit():
        return float(retry_after)
    if resp.headers.get("x-ratelimit-remaining") == "0":
        reset = resp.headers.get("x-ratelimit-reset")
        if reset and reset.isdigit():
            return max(0.0, float(reset) - time.time()) + 1.0
    return None


class MinIntervalLimiter:
    def __init__(self, min_interval: float):
        self._interval = min_interval
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
            self._next = time.monotonic() + self._interval


async def fetch(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    expect: str = "text",  # "text" | "json"
    max_retries: int = 5,
    allow_404: bool = False,
    timeout_s: float = 30,
    max_wait_s: float = 300,
) -> Any:
    """GET with retries. Returns text/JSON, or None on 404 when allow_404=True."""
    for attempt in range(max_retries + 1):
        try:
            async with session.get(
                url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout_s)
            ) as resp:
                if resp.status == 200:
                    return await (resp.json(content_type=None) if expect == "json" else resp.text())
                if resp.status == 404 and allow_404:
                    return None
                body = (await resp.text())[:200]
                server_delay = _server_delay(resp)
                # 403 is only retryable when it is a rate-limit response
                retryable = resp.status in RETRYABLE_STATUS or (resp.status == 403 and server_delay is not None)
                if not retryable or attempt == max_retries:
                    raise HttpError(resp.status, url, body)
                delay = server_delay + random.uniform(0, 1) if server_delay is not None else backoff_delay(attempt)
                reason = f"HTTP {resp.status}"
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == max_retries:
                raise
            delay = backoff_delay(attempt)
            reason = type(exc).__name__
        if delay > max_wait_s:
            raise HttpError(0, url, f"required wait {delay:.0f}s exceeds cap of {max_wait_s:.0f}s")
        log.warning("%s on %s; retry %d/%d in %.1fs", reason, url, attempt + 1, max_retries, delay)
        await asyncio.sleep(delay)
    raise RuntimeError("unreachable")
