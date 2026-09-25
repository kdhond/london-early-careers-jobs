"""
Categorisation (§7): assigning each job to exactly one general TYPE
category (Software Engineering, Data & Analytics, Finance & Accounting,
etc.) — this is the public, general-purpose classification shown as tabs
on the site. It is entirely separate from `jobboard/relevance.py`'s
`is_relevant` flag, which answers a different, personal question ("is this
relevant to Kailas's biotech/VC/health-equity search"). A job gets both.

The rules live in config/categories.yaml, not here — this file just reads
that ordered list and, for each job, walks down it looking for the first
category whose keywords match. "First match wins" is the whole algorithm;
config/categories.yaml is where the actual tuning happens (add a keyword,
reorder a category to be checked earlier, etc.) without touching this file.
"""
from __future__ import annotations

import re

from jobboard.models import Job
from jobboard.sources.base import load_yaml

FALLBACK_CATEGORY = "Other"


def _build_keyword_pattern(keywords: list[str]) -> re.Pattern:
    """One regex that matches ANY of a category's keyword phrases, as whole words/phrases."""
    escaped = [re.escape(keyword) for keyword in keywords]
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


class Categoriser:
    """
    Loads config/categories.yaml once and reuses the compiled regex for
    every job in a refresh, rather than re-parsing YAML and recompiling
    regexes per job (there can be thousands of jobs per refresh).
    """

    def __init__(self, categories: list[dict] | None = None):
        self.categories = categories if categories is not None else load_yaml("categories.yaml")
        self._patterns = [
            (category["name"], _build_keyword_pattern(category["keywords"]))
            for category in self.categories
            if category["keywords"]  # "Other" has no keywords — it's the fallback, not a rule
        ]

    def categorise(self, job: Job) -> str:
        for name, pattern in self._patterns:
            if pattern.search(job.title or ""):
                return name
        for name, pattern in self._patterns:
            if pattern.search(job.description_text or ""):
                return name
        return FALLBACK_CATEGORY


def categorise_jobs(jobs: list[Job], categoriser: Categoriser | None = None) -> None:
    """Assign `category` on every job in place (used by refresh.py)."""
    categoriser = categoriser or Categoriser()
    for job in jobs:
        job.category = categoriser.categorise(job)
