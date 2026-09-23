"""
Duplicate removal (§6.4).

The same real-world job often shows up more than once in a single refresh:
once from the company's own Greenhouse/Lever/Ashby board, once from
LinkedIn, once from Reed, once from Adzuna. This module's job is to spot
those and merge them into a single Job before they ever reach the
database.

The approach is a two-level clustering:
  1. Group jobs by company — first by an exact match on the normalised
     company name (models.normalise_company), then by fuzzily merging
     any *other* normalised names that are close enough (e.g. "Monzo" and
     "Monzo Bank" both end up in one company cluster).
  2. Within each company cluster, group jobs by title + city — two jobs
     are considered the same posting if their (specially normalised)
     titles are a close fuzzy match AND their cities aren't clearly
     different.

Each resulting group of 1+ jobs is merged into a single Job. Deliberately
NOT merged: two different roles at the same company whose titles differ
in a meaningful way (e.g. "Graduate Software Engineer – Backend" vs "...
– iOS") — see _strip_known_suffix below for how that distinction is kept.
"""
from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import fuzz

from jobboard.models import ApplyLink, Job

# Lower number = prefer this source's *details* (description, salary, etc.)
# when merging a group — company career pages are always full & authoritative,
# then LinkedIn, then Reed (once we've fetched its full description), then
# Adzuna (snippet-only, so lowest priority).
_SOURCE_PRIORITY = {
    "greenhouse": 0,
    "lever": 0,
    "ashby": 0,
    "linkedin": 1,
    "reed": 2,
    "adzuna": 3,
}
_DEFAULT_SOURCE_PRIORITY = 99  # unknown source names sort last


# ---------------------------------------------------------------------------
# Title normalisation for matching (deliberately different from, and more
# aggressive than, models.normalise_company — this is only used to decide
# "are these titles the same job", never stored or displayed).
# ---------------------------------------------------------------------------

_BRACKETS_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
# Typical ATS reference codes trailing a title, e.g. "REQ1234", "REF-12345", "Job 4457313776".
_TRAILING_CODE_RE = re.compile(r"\s*[-–—]?\s*\b(?:req|ref|job|id)?[-\s]?\d{3,}\b\s*$", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

# Words that, when they're the *entire* trailing "- word(s)" suffix of a
# title, are almost always a location or work-mode annotation rather than
# part of the actual role (e.g. "Software Engineer - London"). We only
# strip the suffix if every word in it is one of these — a suffix like
# "- Backend" or "- iOS" is NOT in this list, so titles that differ only
# by that kind of suffix are correctly kept apart (they're different roles).
_LOCATION_MODE_WORDS = {
    "london", "remote", "hybrid", "onsite", "on-site", "uk", "england",
    "manchester", "birmingham", "leeds", "bristol", "cambridge", "oxford",
    "reading", "brighton", "newcastle", "nottingham", "sheffield",
    "liverpool", "southampton", "leicester", "coventry", "york", "bath",
    "guildford", "milton keynes",
}
_TRAILING_DASH_SUFFIX_RE = re.compile(r"[-–—]\s*([A-Za-z][A-Za-z\s]{1,25})$")


def _strip_known_suffix(title: str) -> str:
    """Remove a trailing "- London" / "- Hybrid"-style suffix, but only if it's a known location/mode word."""
    match = _TRAILING_DASH_SUFFIX_RE.search(title)
    if match and match.group(1).strip().lower() in _LOCATION_MODE_WORDS:
        return title[: match.start()].strip()
    return title


def normalise_title_for_dedupe(title: str) -> str:
    text = title or ""
    text = _BRACKETS_RE.sub(" ", text)      # "(Hybrid)", "(London)", "[Ref: 123]" -> gone
    text = _TRAILING_CODE_RE.sub("", text)  # trailing "REQ1234" etc -> gone
    text = _strip_known_suffix(text)        # trailing "- London" -> gone; "- Backend" stays
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _title_similarity(a: str, b: str) -> float:
    # token_sort_ratio (not token_set_ratio): token_set_ratio treats one
    # title's tokens as a subset of the other's whenever they overlap a
    # lot, which sounds right but actually inflates the score for titles
    # that differ by exactly one distinguishing word — "Graduate Software
    # Engineer – Backend" vs "... – iOS" scores ~93 on token_set_ratio
    # (wrongly above a threshold of 90) but ~81 on token_sort_ratio
    # (correctly below it). Since our title normalisation already strips
    # the location/mode suffixes that legitimately differ between
    # duplicate postings of the *same* job (see normalise_title_for_dedupe),
    # token_sort_ratio's word-order tolerance is all we need here.
    return fuzz.token_sort_ratio(normalise_title_for_dedupe(a), normalise_title_for_dedupe(b))


def _company_similarity(a: str, b: str) -> float:
    # token_set_ratio here IS what we want: "Monzo" should be recognised
    # as the same company as "Monzo Bank" (one name's tokens are a subset
    # of the other's), which plain ratio scores far too low (~67) to pass
    # a sensible threshold.
    return fuzz.token_set_ratio(a, b)


def _compatible_city(a: Optional[str], b: Optional[str]) -> bool:
    """
    Two jobs can only be the same posting if their cities don't clearly
    conflict. Expects normalised city names (filters.normalise_city),
    e.g. "London", "Manchester", "Remote (UK)" — not raw, differently
    formatted location strings, which would rarely match each other even
    for the exact same city.
    """
    if not a or not b:
        return True  # unknown city on either side — don't let it block a match
    return a.strip().lower() == b.strip().lower()


# ---------------------------------------------------------------------------
# Union-find (disjoint set) — the standard structure for "merge these two
# items into the same group" style clustering.
# ---------------------------------------------------------------------------


class _UnionFind:
    def __init__(self, n: int):
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]  # path compression
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def dedupe_jobs(jobs: list[Job], settings: dict) -> list[Job]:
    """
    Merge duplicate postings of the same job across sources into one Job
    each. Order of the input list doesn't matter; order of the output
    isn't guaranteed either (refresh.py sorts jobs for display separately).
    """
    if not jobs:
        return []

    company_threshold = settings["dedupe"]["company_threshold"]
    title_threshold = settings["dedupe"]["title_threshold"]

    company_cluster_of = _cluster_companies(jobs, company_threshold)

    # Only compare titles between jobs in the *same* company cluster —
    # comparing every job against every other job regardless of company
    # would be both slow and wrong (two unrelated companies can easily
    # have similarly-worded job titles).
    jobs_by_company_cluster: dict[int, list[int]] = {}
    for i, cluster_id in enumerate(company_cluster_of):
        jobs_by_company_cluster.setdefault(cluster_id, []).append(i)

    uf = _UnionFind(len(jobs))
    for indices in jobs_by_company_cluster.values():
        for a in range(len(indices)):
            for b in range(a + 1, len(indices)):
                i, j = indices[a], indices[b]
                if not _compatible_city(jobs[i].city, jobs[j].city):
                    continue
                if _title_similarity(jobs[i].title, jobs[j].title) >= title_threshold:
                    uf.union(i, j)

    groups: dict[int, list[Job]] = {}
    for i, job in enumerate(jobs):
        groups.setdefault(uf.find(i), []).append(job)

    return [_merge_group(group) for group in groups.values()]


def _cluster_companies(jobs: list[Job], threshold: float) -> list[int]:
    """
    Returns, for each job, an integer id identifying which "company
    cluster" it belongs to (two jobs get the same id if their normalised
    company names are identical or fuzzily close enough).
    """
    unique_names = sorted({job.company_normalised for job in jobs})
    name_uf = _UnionFind(len(unique_names))
    for a in range(len(unique_names)):
        for b in range(a + 1, len(unique_names)):
            if _company_similarity(unique_names[a], unique_names[b]) >= threshold:
                name_uf.union(a, b)

    name_to_index = {name: i for i, name in enumerate(unique_names)}
    return [name_uf.find(name_to_index[job.company_normalised]) for job in jobs]


def _merge_group(group: list[Job]) -> Job:
    if len(group) == 1:
        return group[0]

    primary = min(group, key=_primary_sort_key)

    all_apply_links: list[ApplyLink] = []
    seen_links = set()
    all_sources: list[str] = []
    seen_sources = set()
    earliest_posted_at = None

    for job in group:
        for link in job.apply_links:
            key = (link.source, link.url)
            if key not in seen_links:
                seen_links.add(key)
                all_apply_links.append(link)
        for source in job.sources:
            if source not in seen_sources:
                seen_sources.add(source)
                all_sources.append(source)
        if job.posted_at and (earliest_posted_at is None or job.posted_at < earliest_posted_at):
            earliest_posted_at = job.posted_at

    merged = Job(
        id=primary.id,
        title=primary.title,
        company=primary.company,
        company_normalised=primary.company_normalised,
        location=primary.location,
        city=primary.city,
        work_mode=primary.work_mode,
        posted_at=earliest_posted_at or primary.posted_at,
        posted_at_estimated=primary.posted_at_estimated,
        salary_min=primary.salary_min,
        salary_max=primary.salary_max,
        salary_text=primary.salary_text,
        employment_type=primary.employment_type,
        seniority=primary.seniority,
        years_required=primary.years_required,
        description_html=primary.description_html,
        description_text=primary.description_text,
        description_is_full=primary.description_is_full,
        requirements=primary.requirements,
        company_website=primary.company_website,
        company_logo=primary.company_logo,
        apply_links=all_apply_links,
        sources=all_sources,
    )

    # Fill in anything the primary record was missing from the other
    # records in the group (e.g. the company page has no salary, but
    # the Adzuna copy of the same job does).
    for job in group:
        if job is primary:
            continue
        if not merged.company_website and job.company_website:
            merged.company_website = job.company_website
        if not merged.company_logo and job.company_logo:
            merged.company_logo = job.company_logo
        if merged.salary_min is None and job.salary_min is not None:
            merged.salary_min = job.salary_min
        if merged.salary_max is None and job.salary_max is not None:
            merged.salary_max = job.salary_max
        if not merged.salary_text and job.salary_text:
            merged.salary_text = job.salary_text
        if merged.work_mode == "unknown" and job.work_mode != "unknown":
            merged.work_mode = job.work_mode
        if not merged.employment_type and job.employment_type:
            merged.employment_type = job.employment_type
        if not merged.seniority and job.seniority:
            merged.seniority = job.seniority
        if not merged.city and job.city:
            merged.city = job.city

    return merged


def _primary_sort_key(job: Job) -> tuple:
    source_priority = min(
        (_SOURCE_PRIORITY.get(s, _DEFAULT_SOURCE_PRIORITY) for s in job.sources),
        default=_DEFAULT_SOURCE_PRIORITY,
    )
    # Sorting ascending: full descriptions first (False < True, so "not
    # is_full" puts full descriptions first), then by source priority,
    # then by longest description (negated, since we sort ascending).
    return (not job.description_is_full, source_priority, -len(job.description_text or ""))
