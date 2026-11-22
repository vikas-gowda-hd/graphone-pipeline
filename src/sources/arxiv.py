"""arXiv API client (Atom feed). Manual: https://info.arxiv.org/help/api/user-manual.html
arXiv asks for at most one request every 3 seconds.
"""
from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Tuple

import aiohttp

from ..http_utils import MinIntervalLimiter, fetch

log = logging.getLogger(__name__)

ARXIV_API_URL = os.getenv("ARXIV_API_URL", "https://export.arxiv.org/api/query")

_CATS = "cat:cs.LG OR cat:cs.CV OR cat:cs.CL OR cat:cs.AI OR cat:stat.ML"
# Preferred: AI/ML papers whose abstract or author comment mentions github.
DEFAULT_QUERY = f'({_CATS}) AND (abs:"github.com" OR co:"github.com" OR abs:github OR co:github)'
# Fallback if arXiv's search index doesn't match URLs the way we expect: fetch by category,
# filter for GitHub links locally.
FALLBACK_QUERY = f"({_CATS})"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}


class ArxivError(Exception):
    pass


@dataclass
class ArxivPaper:
    arxiv_id: str
    title: str
    authors: List[str]
    summary: str
    comment: str
    abs_url: str
    published: datetime


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def parse_feed(xml_text: str) -> Tuple[List[ArxivPaper], int]:
    root = ET.fromstring(xml_text)
    total = int(root.findtext("opensearch:totalResults", default="0", namespaces=NS) or 0)
    papers: List[ArxivPaper] = []
    for entry in root.findall("atom:entry", NS):
        id_url = entry.findtext("atom:id", default="", namespaces=NS)
        if "/api/errors" in id_url:
            raise ArxivError(_clean(entry.findtext("atom:summary", default="", namespaces=NS)))
        arxiv_id = re.sub(r"v\d+$", "", id_url.rsplit("/abs/", 1)[-1])
        published_raw = entry.findtext("atom:published", default="", namespaces=NS)
        if not arxiv_id or not published_raw:
            continue
        published = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        papers.append(
            ArxivPaper(
                arxiv_id=arxiv_id,
                title=_clean(entry.findtext("atom:title", default="", namespaces=NS)),
                authors=[_clean(a.findtext("atom:name", default="", namespaces=NS)) for a in entry.findall("atom:author", NS)],
                summary=_clean(entry.findtext("atom:summary", default="", namespaces=NS)),
                comment=_clean(entry.findtext("arxiv:comment", default="", namespaces=NS)),
                abs_url=f"https://arxiv.org/abs/{arxiv_id}",
                published=published,
            )
        )
    return papers, total


async def fetch_page(
    session: aiohttp.ClientSession,
    limiter: MinIntervalLimiter,
    *,
    query: str,
    start: int,
    page_size: int,
) -> Tuple[List[ArxivPaper], int]:
    params = {
        "search_query": query,
        "start": start,
        "max_results": page_size,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    await limiter.wait()
    text = await fetch(session, ARXIV_API_URL, params=params, max_retries=6)
    return parse_feed(text)
