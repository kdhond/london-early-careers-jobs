"""
Normalised Job schema shared by every source.

Every job source (LinkedIn, Reed, Adzuna, Greenhouse, Lever, Ashby) fetches
data in its own shape, and the first thing each source does is convert its
raw results into a `Job` object using this one schema. That way the rest of
the app (filtering, deduping, categorising, the API, the frontend) only ever
has to deal with one consistent shape of data.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

# Regex to strip common corporate suffixes ("Ltd", "PLC", "Group", "UK", etc.)
# so "Monzo Bank Ltd" and "Monzo" normalise to the same thing.
_COMPANY_SUFFIXES = re.compile(
    r"\b(ltd|limited|plc|inc|incorporated|group|uk)\b", re.IGNORECASE
)
# Anything that isn't a letter/number/whitespace (punctuation, brackets, etc).
_PUNCT = re.compile(r"[^\w\s]")
# One or more whitespace characters, used to collapse "a   b" into "a b".
_WS = re.compile(r"\s+")


def normalise_company(name: str) -> str:
    """
    Turn a company name into a canonical lowercase form so the same company
    written differently by different sources ("Monzo Bank Ltd" vs "Monzo")
    is recognised as the same company during deduplication.
    """
    if not name:
        return ""
    out = name.lower()
    out = _PUNCT.sub(" ", out)          # remove punctuation
    out = _COMPANY_SUFFIXES.sub(" ", out)  # remove "ltd", "plc", etc.
    out = _WS.sub(" ", out).strip()     # collapse repeated spaces
    return out


def make_job_id(company_normalised: str, title: str, location: str) -> str:
    """
    Build a stable, deterministic ID for a job from its normalised company,
    title and location. Because it's a hash of those three fields (not a
    random UUID), the *same* job will always produce the *same* ID even if
    we see it again in a later refresh — that's how the database knows to
    update an existing row instead of creating a duplicate.
    """
    # Normalise the title the same way we normalise company names, so
    # "Software Engineer" and "software engineer!" hash to the same ID.
    title_norm = _WS.sub(" ", _PUNCT.sub(" ", (title or "").lower())).strip()
    location_norm = _WS.sub(" ", (location or "").lower()).strip()
    key = f"{company_normalised}|{title_norm}|{location_norm}"
    # sha256 gives a long hex string; we only keep the first 24 characters
    # since we just need something short and collision-unlikely, not secure.
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


@dataclass
class ApplyLink:
    """One place you can go to apply for a job (e.g. LinkedIn, or the company's own site)."""
    source: str  # which source this link came from, e.g. "linkedin", "reed"
    url: str

    def to_dict(self) -> dict:
        return {"source": self.source, "url": self.url}


@dataclass
class Job:
    """
    The normalised representation of a single job listing.

    `id`, `title`, `company`, `company_normalised` and `location` are
    required; everything else has a sensible default because not every
    source can supply every field (e.g. Adzuna never gives a full
    description, so `description_is_full` stays False for those jobs).
    """
    id: str
    title: str
    company: str
    company_normalised: str
    location: str

    company_website: Optional[str] = None
    company_logo: Optional[str] = None
    city: Optional[str] = None  # normalised city, e.g. "London", "Remote (UK)"
    work_mode: str = "unknown"  # onsite / hybrid / remote / unknown

    posted_at: Optional[str] = None  # ISO date string, e.g. "2026-09-20"
    posted_at_estimated: bool = False  # True if we guessed this from first-seen date
    first_seen_at: Optional[str] = None  # set automatically by the database
    last_seen_at: Optional[str] = None   # set automatically by the database

    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_text: Optional[str] = None  # original salary text, for display

    employment_type: Optional[str] = None  # full-time / internship / contract / ...
    seniority: Optional[str] = None        # from the source, e.g. "Entry level"
    years_required: Optional[int] = None   # minimum years of experience, if stated
    category: str = "Other"                # assigned by categorise.py

    description_html: str = ""   # sanitised HTML, safe to render in the browser
    description_text: str = ""   # plain text version, used for searching/matching
    description_is_full: bool = False  # False if this is just a short snippet
    requirements: list[str] = field(default_factory=list)  # bullet points pulled from the description

    apply_links: list[ApplyLink] = field(default_factory=list)  # every place this job can be applied to
    sources: list[str] = field(default_factory=list)  # e.g. ["linkedin", "reed"] if seen on both
    is_active: bool = True  # set to False once a job disappears from all sources

    def to_dict(self) -> dict:
        """Convert to a plain dict (e.g. for turning into JSON for the API)."""
        d = asdict(self)
        # asdict() would otherwise leave ApplyLink objects as dataclasses,
        # not plain dicts, so convert those explicitly.
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
        """
        Convenience constructor used by every source: pass in the raw title,
        company and location, and this figures out `company_normalised` and
        `id` for you, and starts `sources` with just this one source.
        """
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
