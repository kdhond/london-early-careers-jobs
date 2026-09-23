"""
Filtering: deciding which jobs belong on the board at all.

Two independent filters run on every job during a refresh (see
refresh.py): the experience filter (§6.1 — "is this actually an
early-career role?") and the location filter (§6.2 — "is this actually in
England?"). Both take a `settings` dict (loaded from config/settings.yaml)
so thresholds can be tuned without touching this code.

Both filters return a plain bool, and refresh.py is responsible for
logging how many jobs each one removes (so the thresholds can be tuned
over time, per the spec).
"""
from __future__ import annotations

import re

from jobboard.extract import extract_years_required
from jobboard.models import Job

# ---------------------------------------------------------------------------
# Experience filter (§6.1)
# ---------------------------------------------------------------------------

# Seniority values (as given directly by a source, e.g. LinkedIn's
# `experienceLevel`) that mean "not early career", regardless of what the
# title says.
_SENIOR_SENIORITY_VALUES = {"mid-senior level", "director", "executive"}


def _build_excluded_title_re(excluded_words: list[str]) -> re.Pattern:
    """
    Build a single whole-word, case-insensitive regex from the configured
    excluded-title-words list (e.g. ["senior", "sr", "lead", ...]). Using
    \\b word boundaries means "lead" excludes "Team Lead" but not
    "Leadership Development", and multi-word phrases like "head of" are
    matched as a phrase.
    """
    escaped = [re.escape(word) for word in excluded_words]
    pattern = r"\b(?:" + "|".join(escaped) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


def is_early_career(job: Job, settings: dict) -> bool:
    """
    True if this job should be kept under the 0-4 years experience filter.

    A job is kept only if ALL of these hold:
      1. Its title doesn't contain an excluded seniority word ("senior", "lead", ...).
      2. Its source-given seniority (if any) isn't Mid-Senior/Director/Executive.
      3. Its minimum years required (extracted from the description) is
         <= max_years, or wasn't stated at all.
    """
    experience_settings = settings["experience"]
    excluded_words = experience_settings["excluded_title_words"]
    max_years = experience_settings["max_years"]

    excluded_title_re = _build_excluded_title_re(excluded_words)
    if excluded_title_re.search(job.title or ""):
        return False

    if job.seniority and job.seniority.strip().lower() in _SENIOR_SENIORITY_VALUES:
        return False

    # Prefer years_required if a source or an earlier extraction pass has
    # already set it; otherwise pull it from the description now.
    years_required = job.years_required
    if years_required is None:
        years_required = extract_years_required(job.description_text)
        job.years_required = years_required  # cache it on the job for display/debugging

    if years_required is not None and years_required > max_years:
        return False

    return True


# ---------------------------------------------------------------------------
# Location filter (§6.2)
# ---------------------------------------------------------------------------

_REMOTE_UK_RE = re.compile(r"remote.*(uk|united kingdom|england)|(uk|united kingdom|england).*remote", re.IGNORECASE)

# Other UK nations, plus a handful of other countries that show up a lot
# in the global company boards (Greenhouse/Lever/Ashby jobs for companies
# like Cloudflare or OpenAI include worldwide listings). This list exists
# mainly to stop city-name collisions like "New York" matching the England
# city "York" — checked *before* the England-city match, so an explicit
# non-England country always wins.
_NOT_ENGLAND_RE = re.compile(
    r"\b(scotland|glasgow|edinburgh|aberdeen|dundee|"
    r"wales|cardiff|swansea|newport|"
    r"northern ireland|belfast|"
    r"united states|usa|u\.s\.a?\.?|"
    r"canada|ireland|germany|france|spain|netherlands|poland|"
    r"india|singapore|australia|new zealand|south africa|"
    r"japan|china|brazil|mexico|portugal|"
    r"new york|san francisco|los angeles|chicago|boston|seattle|austin|toronto|dublin|berlin|paris|amsterdam|bangalore|singapore)\b",
    re.IGNORECASE,
)


def is_in_england(job: Job, settings: dict) -> bool:
    """
    True if this job's location looks like it's in England (or a
    remote-UK role), false if it's clearly in Scotland/Wales/Northern
    Ireland or outside the UK entirely.

    We only have free-text location strings to work with (no geocoding),
    so this is a keyword match against config/settings.yaml's list of
    English cities/regions plus "London"/"England"/"UK", with an explicit
    exclusion list for the other UK nations so e.g. "Manchester" doesn't
    accidentally also match a Glasgow listing that happens to mention
    "United Kingdom".
    """
    location = (job.location or "").strip()
    if not location:
        return False  # no location at all — can't confirm it's in England

    if _NOT_ENGLAND_RE.search(location):
        return False

    if _REMOTE_UK_RE.search(location):
        return True

    england_cities = settings["locations"]["england_cities"]
    keywords = [*england_cities, "england", "united kingdom", "uk"]
    # Word-boundary match, not a plain substring check — otherwise e.g.
    # "New York" would wrongly match the England city "York".
    keywords_re = re.compile(
        r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")\b", re.IGNORECASE
    )
    return bool(keywords_re.search(location))


def normalise_city(location: str, settings: dict) -> str:
    """
    Pull a canonical city name out of a raw location string, e.g. "London,
    England, United Kingdom" -> "London". This runs once per job right
    after fetching (see refresh.py's "normalise" pipeline step) and its
    result — job.city — is what dedupe.py actually compares, instead of
    trying to fuzzy-match raw, differently-formatted location strings from
    different sources.
    """
    if not location:
        return ""
    location_lower = location.lower()
    if _REMOTE_UK_RE.search(location) or re.search(r"\bremote\b", location_lower):
        return "Remote (UK)"

    england_cities = settings["locations"]["england_cities"]
    for city in england_cities:
        if re.search(rf"\b{re.escape(city.lower())}\b", location_lower):
            return city

    # No known city name found — fall back to whatever's before the first
    # comma (usually the most specific part of the raw location string).
    return location.split(",")[0].strip()
