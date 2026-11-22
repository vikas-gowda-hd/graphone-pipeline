"""Research-paper pipeline.

arXiv (title/authors/date)  ->  GitHub link found IN the paper's own abstract/comment
                            ->  GitHub REST API (current star count)

No LLM is used here, and nothing is inferred: every github_url must literally appear in the
arXiv record, and every star count comes from the GitHub API. That is the anti-hallucination
guarantee for this vertical.

Resumable: records are appended to data/papers.jsonl and the arXiv offset is checkpointed,
so a crash/restart continues where it stopped.

Run:  python -m src.pipelines.papers --target 1100
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
from pathlib import Path
from typing import Optional, Set, Tuple

import aiohttp
from pydantic import ValidationError

from ..config import DATA_DIR, settings
from ..http_utils import MinIntervalLimiter
from ..logging_setup import setup_logging
from ..schemas import PaperRecord
from ..sources import arxiv
from ..sources.github import GitHubClient, RepoInfo, extract_repo_urls

log = logging.getLogger("papers")

CSV_COLUMNS = [
    "title", "authors", "paper_url", "github_url", "github_stars",
    "github_stars_fetched_at", "published_date", "source_name", "source_url", "collectedAt",
]


def build_record(paper: arxiv.ArxivPaper, info: Optional[RepoInfo]) -> PaperRecord:
    return PaperRecord(
        source={"name": "arXiv", "url": paper.abs_url},
        content={
            "title": paper.title,
            "authors": paper.authors,
            "paper_url": paper.abs_url,
            "github_url": info.url if info else None,
            "github_stars": info.stars if info else None,
            "github_stars_fetched_at": info.fetched_at if info else None,
            "published_date": paper.published,
        },
    )


async def _process(paper: arxiv.ArxivPaper, gh: GitHubClient, require_repo: bool) -> Optional[PaperRecord]:
    try:
        info = None
        for url in extract_repo_urls(paper.summary, paper.comment)[:3]:
            info = await gh.repo_info(url)
            if info:
                break
        if info is None and require_repo:
            return None
        return build_record(paper, info)
    except ValidationError as exc:
        log.warning("Dropping %s: schema validation failed: %s", paper.abs_url, exc.errors()[:1])
    except Exception:  # one bad paper must never kill the run
        log.exception("Unexpected error processing %s", paper.abs_url)
    return None


def _load_seen(jsonl: Path) -> Set[str]:
    seen: Set[str] = set()
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(json.loads(line)["source"]["url"])
    return seen


def _load_checkpoint(path: Path, query: str, fallback: str) -> Tuple[int, str]:
    if path.exists():
        data = json.loads(path.read_text())
        if data.get("query") in (query, fallback):
            return int(data.get("start", 0)), data["query"]
    return 0, query


def write_csv(jsonl: Path, csv_path: Path) -> int:
    rows = 0
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            c = r["content"]
            writer.writerow({
                "title": c["title"], "authors": "; ".join(c["authors"]), "paper_url": c["paper_url"],
                "github_url": c["github_url"] or "", "github_stars": "" if c["github_stars"] is None else c["github_stars"],
                "github_stars_fetched_at": c["github_stars_fetched_at"] or "", "published_date": c["published_date"],
                "source_name": r["source"]["name"], "source_url": r["source"]["url"], "collectedAt": r["collectedAt"],
            })
            rows += 1
    return rows


async def run(
    *,
    target: int = 1100,
    page_size: int = 100,
    concurrency: Optional[int] = None,
    query: str = arxiv.DEFAULT_QUERY,
    fallback_query: str = arxiv.FALLBACK_QUERY,
    max_pages: int = 80,
    require_repo: bool = True,
    fresh: bool = False,
    out_dir: Path = DATA_DIR,
    arxiv_interval: float = 3.1,
) -> dict:
    concurrency = concurrency or settings.max_concurrency
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl, ckpt, csv_path = out_dir / "papers.jsonl", out_dir / "papers.checkpoint.json", out_dir / "papers.csv"
    if fresh:
        for p in (jsonl, ckpt, csv_path):
            p.unlink(missing_ok=True)

    seen = _load_seen(jsonl)
    start, active_query = _load_checkpoint(ckpt, query, fallback_query)
    kept = len(seen)
    pages = 0
    log.info("Starting: target=%d, already have=%d, resume offset=%d", target, kept, start)

    async with aiohttp.ClientSession(headers={"User-Agent": settings.user_agent}, trust_env=True) as session:
        limiter = MinIntervalLimiter(arxiv_interval)
        gh = GitHubClient(session, settings.github_token, concurrency)

        while kept < target and pages < max_pages:
            batch, total = await arxiv.fetch_page(session, limiter, query=active_query, start=start, page_size=page_size)
            pages += 1
            if not batch:
                log.info("arXiv has no more results (start=%d, total=%d)", start, total)
                break

            with_links = sum(1 for p in batch if extract_repo_urls(p.summary, p.comment))
            log.info("page %d: start=%d got=%d with_github_link=%d (arXiv total=%d)", pages, start, len(batch), with_links, total)

            # If the filtered query isn't matching GitHub links, fall back to category-only + local filtering.
            if start == 0 and active_query != fallback_query and len(batch) >= min(page_size, 20) and with_links == 0:
                log.warning("Filtered query returned no GitHub links; switching to category-only query with local filtering")
                active_query = fallback_query
                continue

            todo = [p for p in batch if p.abs_url not in seen]
            results = await asyncio.gather(*(_process(p, gh, require_repo) for p in todo))
            new = [r for r in results if r is not None]
            with jsonl.open("a", encoding="utf-8") as fh:
                for rec in new:
                    fh.write(rec.model_dump_json() + "\n")
                    seen.add(rec.source.url)
            kept += len(new)
            start += len(batch)
            ckpt.write_text(json.dumps({"query": active_query, "start": start}))
            log.info("page %d done: new=%d total_kept=%d/%d", pages, len(new), kept, target)

    rows = write_csv(jsonl, csv_path) if jsonl.exists() else 0
    log.info("Finished: %d papers in %s (CSV: %s)", rows, jsonl, csv_path)
    return {"papers": rows, "pages": pages, "csv": str(csv_path), "jsonl": str(jsonl)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect arXiv papers with GitHub repos and star counts.")
    ap.add_argument("--target", type=int, default=1100, help="stop after this many papers (default 1100)")
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=None, help="parallel GitHub lookups")
    ap.add_argument("--query", default=arxiv.DEFAULT_QUERY)
    ap.add_argument("--max-pages", type=int, default=80)
    ap.add_argument("--allow-no-repo", action="store_true", help="also keep papers without a GitHub link")
    ap.add_argument("--fresh", action="store_true", help="ignore previous output/checkpoint and start over")
    args = ap.parse_args()

    setup_logging()
    if not settings.github_token:
        log.warning("GITHUB_TOKEN not set: you are limited to 60 GitHub requests/hour")
    asyncio.run(run(
        target=args.target, page_size=args.page_size, concurrency=args.concurrency, query=args.query,
        max_pages=args.max_pages, require_repo=not args.allow_no_repo, fresh=args.fresh,
    ))


if __name__ == "__main__":
    main()
