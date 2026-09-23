"""Normalised Job schema shared by every source."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

_COMPANY_SUFFIXES = re.compile(
    r"\b(ltd|limited|plc|inc|incorporated|group|uk)\b", re.IGNORECASE
)
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalise_company(name: str) -> str:
    """Lowercase, strip corporate suffixes and punctuation for matching."""
    if not name:
        return ""
    out = name.lower()
    out = _PUNCT.sub(" ", out)
    out = _COMPANY_SUFFIXES.sub(" ", out)
    out = _WS.sub(" ", out).strip()
    return out


def make_job_id(company_normalised: str, title: str, location: str) -> str:
    """Stable hash of the normalised company, title and location."""
    title_norm = _WS.sub(" ", _PUNCT.sub(" ", (title or "").lower())).strip()
    location_norm = _WS.sub(" ", (location or "").lower()).strip()
    key = f"{company_normalised}|{title_norm}|{location_norm}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


@dataclass
class ApplyLink:
    source: str
    url: str

    def to_dict(self) -> dict:
        return {"source": self.source, "url": self.url}


@dataclass
class Job:
    id: str
    title: str
    company: str
    company_normalised: str
    location: str

    company_website: Optional[str] = None
    company_logo: Optional[str] = None
    city: Optional[str] = None
    work_mode: str = "unknown"  # onsite / hybrid / remote / unknown

    posted_at: Optional[str] = None  # ISO date
    posted_at_estimated: bool = False
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None

    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_text: Optional[str] = None

    employment_type: Optional[str] = None
    seniority: Optional[str] = None
    years_required: Optional[int] = None
    category: str = "Other"

    description_html: str = ""
    description_text: str = ""
    description_is_full: bool = False
    requirements: list[str] = field(default_factory=list)

    apply_links: list[ApplyLink] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    is_active: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["apply_links"] = [
            l.to_dict() if isinstance(l, ApplyLink) else l for l in self.apply_links
        ]
        return d

    @classmethod
    def new(
        cls,
        *,
        title: str,
        company: str,
        location: str,
        source: str,
        **kwargs,
    ) -> "Job":
        company_normalised = normalise_company(company)
        job_id = make_job_id(company_normalised, title, location)
        return cls(
            id=job_id,
            title=title,
            company=company,
            company_normalised=company_normalised,
            location=location,
            sources=[source],
            **kwargs,
        )
