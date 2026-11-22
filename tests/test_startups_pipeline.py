import asyncio
import json

from aiohttp import web

from src.pipelines import startups


def _co(name, ts, tags=("AI",), status="Active", slug=None):
    slug = slug or name.lower().replace(" ", "-")
    return {"name": name, "team_size": ts, "tags": list(tags), "status": status, "slug": slug,
            "url": f"https://www.ycombinator.com/companies/{slug}", "website": f"https://{slug}.com",
            "one_liner": f"{name} does things"}


DATA = [
    _co("Alpha AI", 50), _co("Beta", 5), _co("Gamma", -1), _co("Delta", None),
    _co("Not AI", 99, tags=("Fintech",)), _co("Dead Co", 80, status="Inactive"),
    _co("alpha-ai", 7, slug="alpha-ai-2"),  # duplicate name after normalisation
]


def test_selection_filters_orders_and_dedupes():
    chosen, stats = startups.select_companies(DATA, limit=100)
    assert [c["name"] for c in chosen] == ["Alpha AI", "Beta", "Delta", "Gamma"]  # largest first, unknown last
    assert stats["duplicates_dropped"] == 1 and stats["selected"] == 4


def test_unknown_team_size_becomes_none_not_negative():
    chosen, _ = startups.select_companies(DATA)
    recs = {r.content.entityName: r for r in map(startups.to_record, chosen)}
    assert recs["Gamma"].content.data.employeeCount is None
    assert recs["Delta"].content.data.employeeCount is None
    assert recs["Alpha AI"].content.data.employeeCount == 50
    assert recs["Alpha AI"].source.url == "https://www.ycombinator.com/companies/alpha-ai"


def test_limit_applies():
    chosen, _ = startups.select_companies(DATA, limit=2)
    assert len(chosen) == 2


def test_run_end_to_end_with_fake_server(tmp_path):
    async def go():
        async def handler(_):
            return web.Response(text=json.dumps(DATA), content_type="text/plain")
        app = web.Application(); app.router.add_get("/all.json", handler)
        runner = web.AppRunner(app); await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        port = runner.addresses[0][1]
        try:
            return await startups.run(out_dir=tmp_path, url=f"http://127.0.0.1:{port}/all.json")
        finally:
            await runner.cleanup()
    stats = asyncio.run(go())
    assert stats["records"] == 4 and stats["with_employee_count"] == 2
    lines = (tmp_path / "startups.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["recordType"] == "STARTUP"
    assert (tmp_path / "startups.csv").exists()
