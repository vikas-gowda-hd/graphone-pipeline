"""GitHub helpers: pull repo links out of text, then fetch current star counts from the REST API."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp

from ..http_utils import HttpError, fetch
from ..schemas import utcnow

log = logging.getLogger(__name__)

GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")

_REPO_RE = re.compile(
    r"(?<![A-Za-z0-9.-])(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9][A-Za-z0-9_.-]*)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
# github.com/<these>/... are site pages, not user repos
_RESERVED_OWNERS = {
    "features", "topics", "sponsors", "orgs", "marketplace", "collections", "about", "pricing",
    "login", "settings", "explore", "enterprise", "security", "apps", "pulls", "issues",
    "notifications", "site", "readme", "customer-stories", "trending", "search",
}


def extract_repo_urls(*texts: str) -> List[str]:
    """Return unique canonical https://github.com/owner/repo URLs found in the given texts, in order."""
    found: List[str] = []
    seen = set()
    for text in texts:
        for owner, repo in _REPO_RE.findall(text or ""):
            repo = repo.rstrip(".")
            if repo.lower().endswith(".git"):
                repo = repo[:-4]
            if not repo or owner.lower() in _RESERVED_OWNERS:
                continue
            key = f"{owner}/{repo}".lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(f"https://github.com/{owner}/{repo}")
    return found


@dataclass
class RepoInfo:
    url: str
    stars: int
    fetched_at: datetime


class GitHubClient:
    """Fetches star counts with a concurrency cap and per-repo caching (many papers share repos)."""

    def __init__(self, session: aiohttp.ClientSession, token: str = "", concurrency: int = 10):
        self._session = session
        self._sem = asyncio.Semaphore(concurrency)
        self._tasks: Dict[str, "asyncio.Task[Optional[RepoInfo]]"] = {}
        self._headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    async def repo_info(self, url: str) -> Optional[RepoInfo]:
        key = url.lower()
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch(url))
            self._tasks[key] = task
        return await task

    async def _fetch(self, url: str) -> Optional[RepoInfo]:
        owner, repo = url.rstrip("/").split("/")[-2:]
        try:
            async with self._sem:
                data = await fetch(
                    self._session, f"{GITHUB_API_URL}/repos/{owner}/{repo}",
                    headers=self._headers, expect="json", allow_404=True,
                )
        except HttpError as exc:
            log.warning("GitHub lookup failed for %s: %s", url, exc)
            return None
        if data is None:
            log.info("GitHub repo not found (404): %s", url)
            return None
        if "stargazers_count" not in data:
            log.warning("GitHub response for %s had no stargazers_count", url)
            return None
        return RepoInfo(url=data.get("html_url") or url, stars=int(data["stargazers_count"]), fetched_at=utcnow())
