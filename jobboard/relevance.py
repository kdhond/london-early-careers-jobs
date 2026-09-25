"""
Personal relevance flag: is this job relevant to Kailas's own biotech/VC/
health-equity/finance search, regardless of its general `category`.

This used to be the whole of categorise.py before the public site needed a
separate, general job-TYPE taxonomy back (see categorise.py + config/
categories.yaml) — the matching logic itself hasn't changed, just what
question it's answering and which file it lives in. Rules live in
config/relevance.yaml, not here — this file just reads that list and marks
`job.is_relevant` accordingly.
"""
from __future__ import annotations

import re

from jobboard.models import Job
from jobboard.sources.base import load_yaml

# "Founder's Associate", "Chief of Staff" and "Entrepreneur in Residence" are
# generalist, sector-agnostic titles — keyword-matching the title/description
# alone can't tell a biotech one apart from a fintech one. Greenhouse/Lever/
# Ashby jobs carry `category_hint` from config/companies.yaml though, which
# DOES know the company's sector, so we use that as a tiebreaker for exactly
# these titles (Reed/Adzuna/LinkedIn jobs have no category_hint and fall
# through to the ordinary keyword rules below, same as before).
_GENERALIST_STARTUP_ROLE_RE = re.compile(
    r"\bfounders?[' ]?s?\s*associate\b|\bchief of staff\b|\bentrepreneur[' ]?s? in residence\b|\beir\b",
    re.IGNORECASE,
)

# A job at one of the verified biotech/VC/health-equity companies still
# counts as relevant even if no keyword below matches its title (see
# _HINTED_COMPANY_SECTORS below) — EXCEPT a software/hardware engineering
# role, which isn't relevant to this search no matter how good the company
# is. Added 2026-09-25: "i dont want software engineering jobs as relevant
# to me."
_ENGINEERING_ROLE_RE = re.compile(
    r"\bsoftware engineer\b|\bdeveloper\b|\bbackend\b|\bback-end\b|\bfrontend\b|"
    r"\bfront-end\b|\bfull stack\b|\bfull-stack\b|\bmobile engineer\b|\bios\b|"
    r"\bandroid\b|\bdevops\b|\bsre\b|\bsite reliability\b|\bplatform engineer\b|"
    r"\bqa engineer\b|\btest engineer\b|\bhardware engineer\b",
    re.IGNORECASE,
)

# Unlike keyword matching against free-text descriptions (noisy — see
# title_only below), category_hint is curated, verified data: every company
# carrying one of these hints was hand-checked to actually be a biotech-AI /
# biotech-VC / health-equity company (see config/companies.yaml's header).
# So it's trustworthy enough to use twice: as the generalist-title
# tiebreaker above, AND as a final fallback (in is_relevant() below) for any
# OTHER job at one of these companies that no keyword rule happened to
# catch — e.g. an "Application Support Engineer" role at a biotech-AI
# company is still worth flagging relevant, precisely because we already
# know, as a fact, which sector that company is in.
_HINTED_COMPANY_SECTORS = {"biotech-ai", "biotech-vc", "health-equity"}


def _build_keyword_pattern(keywords: list[str]) -> re.Pattern:
    """One regex that matches ANY of the relevance list's keyword phrases, as whole words/phrases."""
    escaped = [re.escape(keyword) for keyword in keywords]
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


class RelevanceChecker:
    """
    Loads config/relevance.yaml once and reuses the compiled regex for
    every job in a refresh, rather than re-parsing YAML and recompiling a
    regex per job (there can be thousands of jobs per refresh).
    """

    def __init__(self, rules: list[dict] | None = None):
        self.rules = rules if rules is not None else load_yaml("relevance.yaml")
        # relevance.yaml has exactly one entry ("Relevant") with keywords;
        # title_only is always true for it, so there's no description-
        # fallback pass here (unlike categorise.py's generic multi-category
        # loop) — kept simple since there's only ever one rule to check.
        keywords = self.rules[0]["keywords"] if self.rules else []
        self._pattern = _build_keyword_pattern(keywords)

    def is_relevant(self, job: Job) -> bool:
        is_hinted_company = (job.category_hint or "") in _HINTED_COMPANY_SECTORS
        is_engineering_role = bool(_ENGINEERING_ROLE_RE.search(job.title or ""))

        if is_hinted_company and not is_engineering_role:
            if _GENERALIST_STARTUP_ROLE_RE.search(job.title or ""):
                return True

        if self._pattern.search(job.title or ""):
            return True

        # No keyword matched, but we still know — as verified fact, not
        # text-matching guesswork — which sector this company is in. Still
        # excludes engineering roles, per the rule above.
        if is_hinted_company and not is_engineering_role:
            return True

        return False


def mark_relevance(jobs: list[Job], checker: RelevanceChecker | None = None) -> None:
    """Set `is_relevant` on every job in place (used by refresh.py)."""
    checker = checker or RelevanceChecker()
    for job in jobs:
        job.is_relevant = checker.is_relevant(job)
