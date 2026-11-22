"""Startup pipeline: Y Combinator company directory, via the community-maintained yc-oss mirror.

Why a mirror instead of scraping ycombinator.com: yc-oss republishes YC's public directory as a
static JSON file refreshed daily (https://github.com/yc-oss/api), so we get ~6,400 structured
records with ONE polite request and put zero load on YC's servers. No LLM is involved, so
nothing here can be hallucinated: every record links back to its own ycombinator.com page.

Selection: AI-tagged, Active companies, ordered by team size (largest first, unknown last),
de-duplicated by normalised name.

Run:  python -m src.pipelines.startups --limit 1500
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
from pydantic import ValidationError

from ..config import DATA_DIR, settings
from ..http_utils import fetch
from ..logging_setup import setup_logging
from ..schemas import StartupRecord

log = logging.getLogger("startups")

YC_ALL_URL = os.getenv(
    "YC_ALL_URL", "https://raw.githubusercontent.com/yc-oss/api/main/companies/all.json"
)

CSV_COLUMNS = [
    "entityName", "rawName", "employeeCount", "website", "description",
    "source_name", "source_url", "collectedAt", "recordType", "schemaVersion",
]


def norm_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _team_size(value) -> Optional[int]:
    """yc-oss uses null or -1 for 'unknown'. Only accept real non-negative integers."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def select_companies(
    companies: List[dict], *, ai_only: bool = True, status: Optional[str] = "Active", limit: int = 1500
) -> Tuple[List[dict], Dict[str, int]]:
    stats = {"total": len(companies), "after_filter": 0, "duplicates_dropped": 0, "selected": 0}
    pool = [
        c for c in companies
        if c.get("name") and c.get("url")
        and (not ai_only or "AI" in (c.get("tags") or []))
        and (status is None or c.get("status") == status)
    ]
    stats["after_filter"] = len(pool)
    pool.sort(key=lambda c: (-(_team_size(c.get("team_size")) if _team_size(c.get("team_size")) is not None else -1), c["name"].lower()))
    seen, chosen = set(), []
    for c in pool:
        key = norm_key(c["name"])
        if not key or key in seen:
            stats["duplicates_dropped"] += 1
            continue
        seen.add(key)
        chosen.append(c)
    chosen = chosen[:limit]
    stats["selected"] = len(chosen)
    return chosen, stats


def to_record(c: dict) -> Optional[StartupRecord]:
    name = " ".join(c["name"].split())
    try:
        return StartupRecord(
            source={"name": "Y Combinator", "url": c["url"]},
            content={
                "entityName": name,  # replaced by the canonical name in the entity-resolution step
                "rawName": name,
                "website": c.get("website") or None,
                "description": (c.get("one_liner") or "").strip() or None,
                "data": {"employeeCount": _team_size(c.get("team_size"))},
            },
        )
    except ValidationError as exc:
        log.warning("Dropping %s: %s", c.get("name"), exc.errors()[:1])
        return None


def write_outputs(records: List[StartupRecord], jsonl: Path, csv_path: Path) -> None:
    with jsonl.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(r.model_dump_json() + "\n")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in records:
            d = r.model_dump(mode="json")
            c = d["content"]
            w.writerow({
                "entityName": c["entityName"], "rawName": c["rawName"] or "",
                "employeeCount": "" if c["data"]["employeeCount"] is None else c["data"]["employeeCount"],
                "website": c["website"] or "", "description": c["description"] or "",
                "source_name": d["source"]["name"], "source_url": d["source"]["url"],
                "collectedAt": d["collectedAt"], "recordType": d["recordType"], "schemaVersion": d["schemaVersion"],
            })


async def run(
    *, limit: int = 1500, ai_only: bool = True, status: Optional[str] = "Active",
    refresh: bool = False, out_dir: Path = DATA_DIR, url: Optional[str] = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_cache = out_dir / "yc_all.json"
    if raw_cache.exists() and not refresh:
        log.info("Using cached directory file %s (use --refresh to re-download)", raw_cache)
        companies = json.loads(raw_cache.read_text(encoding="utf-8"))
    else:
        log.info("Downloading YC directory from %s", url or YC_ALL_URL)
        async with aiohttp.ClientSession(headers={"User-Agent": settings.user_agent}, trust_env=True) as session:
            companies = await fetch(session, url or YC_ALL_URL, expect="json", timeout_s=180)
        if not isinstance(companies, list):
            raise RuntimeError("Unexpected directory format: expected a JSON list")
        raw_cache.write_text(json.dumps(companies), encoding="utf-8")

    chosen, stats = select_companies(companies, ai_only=ai_only, status=status, limit=limit)
    records = [r for r in (to_record(c) for c in chosen) if r is not None]
    write_outputs(records, out_dir / "startups.jsonl", out_dir / "startups.csv")
    stats["records"] = len(records)
    stats["with_employee_count"] = sum(1 for r in records if r.content.data.employeeCount is not None)
    log.info("Startups: %s", stats)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Startups dataset from the YC directory.")
    ap.add_argument("--limit", type=int, default=1500)
    ap.add_argument("--all-sectors", action="store_true", help="include non-AI startups")
    ap.add_argument("--any-status", action="store_true", help="include inactive/acquired companies")
    ap.add_argument("--refresh", action="store_true", help="re-download the directory file")
    args = ap.parse_args()
    setup_logging()
    asyncio.run(run(limit=args.limit, ai_only=not args.all_sectors,
                    status=None if args.any_status else "Active", refresh=args.refresh))


if __name__ == "__main__":
    main()
