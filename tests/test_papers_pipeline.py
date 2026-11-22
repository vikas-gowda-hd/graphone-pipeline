"""Offline end-to-end test: local fake arXiv + GitHub servers, no internet needed."""
import asyncio
import json

from aiohttp import web

from src.http_utils import backoff_delay
from src.pipelines import papers as papers_mod
from src.sources import arxiv, github

PAPERS = [  # (id, summary, comment)
    ("2601.00001", "We release code at https://github.com/foo/bar.", ""),
    ("2601.00002", "No link in abstract.", "Code: github.com/baz/qux"),
    ("2601.00003", "Nothing to see here.", ""),
    ("2601.00004", "Code at https://github.com/gone/repo", ""),
    ("2601.00005", "Another paper using https://github.com/foo/bar too", ""),
    ("2601.00006", "See https://github.com/features/actions", ""),
]
STARS = {"foo/bar": 120, "baz/qux": 5}


def _feed(rows, total):
    entries = "".join(
        f"""<entry><id>http://arxiv.org/abs/{i}v1</id><published>2026-01-01T10:00:00Z</published>
        <title>Paper {i}</title><summary>{s}</summary><author><name>Ada L</name></author>
        <author><name>Alan T</name></author><arxiv:comment>{c}</arxiv:comment></entry>"""
        for i, s, c in rows
    )
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom">'
            f"<opensearch:totalResults>{total}</opensearch:totalResults>{entries}</feed>")


async def _start_fake(*, break_filter=False, rate_limit_first=False):
    state = {"gh_hits": {}, "arxiv_calls": 0, "queries": []}

    async def arxiv_handler(request):
        state["arxiv_calls"] += 1
        q = request.query["search_query"]
        state["queries"].append(q)
        if rate_limit_first and state["arxiv_calls"] == 1:
            return web.Response(status=429, headers={"Retry-After": "0"})
        rows = PAPERS
        if break_filter and "github" in q:  # simulate arXiv search not matching URLs
            rows = [(i, "no links", "") for i, _, _ in PAPERS]
        start, n = int(request.query["start"]), int(request.query["max_results"])
        return web.Response(text=_feed(rows[start:start + n], len(rows)), content_type="application/atom+xml")

    async def gh_handler(request):
        key = f"{request.match_info['owner']}/{request.match_info['repo']}"
        state["gh_hits"][key] = state["gh_hits"].get(key, 0) + 1
        if key not in STARS:
            return web.json_response({"message": "Not Found"}, status=404)
        return web.json_response({"stargazers_count": STARS[key], "html_url": f"https://github.com/{key}"})

    app = web.Application()
    app.router.add_get("/api/query", arxiv_handler)
    app.router.add_get("/repos/{owner}/{repo}", gh_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, runner.addresses[0][1], state


def _run_pipeline(tmp_path, monkeypatch, **fake_kwargs):
    async def go():
        runner, port, state = await _start_fake(**fake_kwargs)
        monkeypatch.setattr(arxiv, "ARXIV_API_URL", f"http://127.0.0.1:{port}/api/query")
        monkeypatch.setattr(github, "GITHUB_API_URL", f"http://127.0.0.1:{port}")
        try:
            stats = await papers_mod.run(target=100, page_size=2, out_dir=tmp_path, arxiv_interval=0)
        finally:
            await runner.cleanup()
        return stats, state
    return asyncio.run(go())


def _read(tmp_path):
    return [json.loads(l) for l in (tmp_path / "papers.jsonl").read_text().splitlines() if l.strip()]


def test_end_to_end_keeps_only_papers_with_verified_repos(tmp_path, monkeypatch):
    stats, state = _run_pipeline(tmp_path, monkeypatch)
    recs = _read(tmp_path)
    assert {r["source"]["url"] for r in recs} == {
        "https://arxiv.org/abs/2601.00001", "https://arxiv.org/abs/2601.00002", "https://arxiv.org/abs/2601.00005"}
    by_id = {r["source"]["url"][-10:]: r["content"] for r in recs}
    assert by_id["2601.00001"]["github_stars"] == 120 and by_id["2601.00001"]["github_url"] == "https://github.com/foo/bar"
    assert by_id["2601.00002"]["github_stars"] == 5
    assert by_id["2601.00001"]["authors"] == ["Ada L", "Alan T"]
    assert state["gh_hits"]["foo/bar"] == 1  # cached across papers 1 and 5
    assert (tmp_path / "papers.csv").exists() and stats["papers"] == 3


def test_resume_does_not_duplicate(tmp_path, monkeypatch):
    _run_pipeline(tmp_path, monkeypatch)
    _run_pipeline(tmp_path, monkeypatch)
    assert len(_read(tmp_path)) == 3


def test_retries_on_429(tmp_path, monkeypatch):
    _, state = _run_pipeline(tmp_path, monkeypatch, rate_limit_first=True)
    assert state["arxiv_calls"] >= 4 and len(_read(tmp_path)) == 3


def test_falls_back_to_category_query(tmp_path, monkeypatch):
    _, state = _run_pipeline(tmp_path, monkeypatch, break_filter=True)
    assert any("github" not in q for q in state["queries"]) and len(_read(tmp_path)) == 3


def test_backoff_bounds():
    for attempt in range(8):
        d = backoff_delay(attempt, base=1, cap=60)
        ceiling = min(60, 2 ** attempt)
        assert ceiling / 2 <= d <= ceiling
