"""Canonical record schemas, version 1.0.

Field names and casing follow the assignment PDF exactly (camelCase for STARTUP/PRODUCT,
snake_case for RESEARCH_PAPER/JOB). Fields marked EXTRA are additions the PDF omits but we
need for provenance and traceability (every record must trace to a real source URL).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(v: datetime) -> datetime:
    """Force timezone-aware UTC. Naive datetimes are assumed to be UTC."""
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc)


def is_fresh(ts: datetime, hours: int = 24, now: Optional[datetime] = None) -> bool:
    """True if ts is within the last `hours` (small clock-skew allowance into the future)."""
    now = now or utcnow()
    ts = _to_utc(ts)
    return now - timedelta(hours=hours) <= ts <= now + timedelta(minutes=5)


class Source(BaseModel):
    name: str = Field(min_length=1)
    url: str

    @field_validator("url")
    @classmethod
    def _must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("source.url must be an http(s) URL")
        return v


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: str = SCHEMA_VERSION
    source: Source  # EXTRA for papers/jobs/news: provenance is mandatory everywhere
    collectedAt: datetime = Field(default_factory=utcnow)

    @field_validator("collectedAt")
    @classmethod
    def _utc_collected(cls, v: datetime) -> datetime:
        return _to_utc(v)


class PricingModel(str, Enum):
    FREE = "FREE"
    FREEMIUM = "FREEMIUM"
    PAID = "PAID"
    ENTERPRISE = "ENTERPRISE"


DateMethod = Literal["api", "meta", "relative", "heuristic"]  # EXTRA: how the date was derived


# ---------- STARTUP ----------
class StartupData(BaseModel):
    employeeCount: Optional[int] = Field(default=None, ge=0)


class StartupContent(BaseModel):
    entityName: str = Field(min_length=1)  # canonical name (after entity resolution)
    rawName: Optional[str] = None  # EXTRA: name as found on source (feeds Entity Mapping Log)
    website: Optional[str] = None  # EXTRA
    description: Optional[str] = None  # EXTRA
    data: StartupData = Field(default_factory=StartupData)


class StartupRecord(_Record):
    recordType: Literal["STARTUP"] = "STARTUP"
    content: StartupContent


# ---------- PRODUCT ----------
class ProductContent(BaseModel):
    startupName: str = Field(min_length=1)  # canonical startup name
    productName: str = Field(min_length=1)  # EXTRA: PDF has no product name field
    pricingModel: PricingModel
    description: Optional[str] = None  # EXTRA


class ProductRecord(_Record):
    recordType: Literal["PRODUCT"] = "PRODUCT"
    content: ProductContent


# ---------- RESEARCH PAPER ----------
class PaperContent(BaseModel):
    title: str = Field(min_length=1)
    authors: List[str] = Field(default_factory=list)
    paper_url: str
    github_url: Optional[str] = None
    github_stars: Optional[int] = Field(default=None, ge=0)
    github_stars_fetched_at: Optional[datetime] = None  # EXTRA: stars are a dynamic metric
    published_date: datetime

    @field_validator("published_date", "github_stars_fetched_at")
    @classmethod
    def _utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _to_utc(v) if v else v


class PaperRecord(_Record):
    recordType: Literal["RESEARCH_PAPER"] = "RESEARCH_PAPER"
    content: PaperContent


# ---------- JOB ----------
class JobContent(BaseModel):
    company: str = Field(min_length=1)  # canonical company name
    title: str = Field(min_length=1)  # EXTRA
    job_url: str  # EXTRA
    date: datetime
    date_method: DateMethod  # EXTRA
    is_remote: bool
    role_family: str  # e.g. "Engineering"
    location: Optional[str] = None  # EXTRA

    @field_validator("date")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


class JobRecord(_Record):
    recordType: Literal["JOB"] = "JOB"
    content: JobContent


# ---------- NEWS (PDF gives no schema; this is our own) ----------
class NewsContent(BaseModel):
    title: str = Field(min_length=1)
    url: str
    publisher: str
    published_date: datetime
    date_method: DateMethod
    full_text: str = Field(min_length=1)

    @field_validator("published_date")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


class NewsRecord(_Record):
    recordType: Literal["NEWS"] = "NEWS"
    content: NewsContent
