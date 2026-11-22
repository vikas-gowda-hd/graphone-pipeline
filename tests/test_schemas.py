from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.schemas import (JobRecord, PaperRecord, PricingModel, ProductRecord,
                         StartupRecord, is_fresh, utcnow)

SRC = {"name": "TestSource", "url": "https://example.com/x"}


def test_startup_roundtrip():
    r = StartupRecord(source=SRC, content={"entityName": "OpenAI", "data": {"employeeCount": 3000}})
    d = r.model_dump(mode="json")
    assert d["recordType"] == "STARTUP" and d["schemaVersion"] == "1.0"
    assert d["content"]["data"]["employeeCount"] == 3000
    assert d["collectedAt"].endswith(("Z", "+00:00"))


def test_product_enum_rejects_invalid():
    ProductRecord(source=SRC, content={"startupName": "A", "productName": "B", "pricingModel": "FREE"})
    with pytest.raises(ValidationError):
        ProductRecord(source=SRC, content={"startupName": "A", "productName": "B", "pricingModel": "CHEAP"})


def test_source_url_must_be_http():
    with pytest.raises(ValidationError):
        StartupRecord(source={"name": "x", "url": "not-a-url"}, content={"entityName": "A"})


def test_naive_dates_become_utc():
    p = PaperRecord(source=SRC, content={"title": "T", "paper_url": "https://arxiv.org/abs/1",
                                         "published_date": "2026-01-01T10:00:00"})
    assert p.content.published_date.tzinfo is not None


def test_freshness_window():
    now = utcnow()
    assert is_fresh(now - timedelta(hours=2))
    assert not is_fresh(now - timedelta(hours=25))
    assert not is_fresh(now + timedelta(hours=3))


def test_job_requires_date_method():
    with pytest.raises(ValidationError):
        JobRecord(source=SRC, content={"company": "A", "title": "t", "job_url": "https://x.com",
                                       "date": utcnow(), "is_remote": True, "role_family": "Engineering"})
