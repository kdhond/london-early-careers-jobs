"""
Extraction: pulling structured facts out of an unstructured job description.

Every source gives us a description as either plain text or (messy,
inconsistent) HTML, and we need to pull four things out of it:
  - `requirements`: a short bullet list of "what you need" (extract_requirements)
  - `years_required`: the minimum years of experience asked for (extract_years_required)
  - salary, when the source didn't already give it as structured data (extract_salary)
  - `work_mode`: hybrid / remote / onsite (detect_work_mode)

None of this is exact — job descriptions are written by humans in wildly
different styles — so every function here is a best-effort heuristic, not
a guarantee. That's fine: the goal is "usually right", not "always right".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Turning HTML (or already-plain text) into a clean list of lines.
# ---------------------------------------------------------------------------

# Tags that represent a line break or the end of a block — we turn all of
# these into a newline before stripping tags, so headings and bullet points
# don't get glued onto the text around them.
_BLOCK_BREAK_RE = re.compile(
    r"</?(br|p|div|li|ul|ol|h1|h2|h3|h4|h5|h6)\s*/?>", re.IGNORECASE
)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_BULLET_PREFIX_RE = re.compile(r"^[\-\*•●‣⁃]\s*|^\d+[\.\)]\s*")
_WS_RE = re.compile(r"[ \t]+")


def _html_to_lines(html_or_text: str) -> list[str]:
    """
    Convert an HTML (or plain-text) description into a list of trimmed,
    non-empty lines, roughly preserving the paragraph/bullet structure the
    original had. This is deliberately simple (regex, not a real HTML
    parser) — good enough for finding headings and bullet points, not
    meant to render anything.
    """
    import html as html_module

    text = html_module.unescape(html_or_text or "")
    text = _BLOCK_BREAK_RE.sub("\n", text)
    text = _ANY_TAG_RE.sub("", text)  # drop remaining tags (e.g. <strong>, <span>)
    lines = [
        _WS_RE.sub(" ", line).strip()
        for line in text.split("\n")
    ]
    return [line for line in lines if line]


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------

# Phrases that, when a line consists of (almost) nothing else, mark the
# start of a requirements-style section. This is deliberately anchored to
# match the *whole* line (give or take a few trailing words like "for
# this role") rather than just searching for the phrase anywhere — a bare
# substring match on something like "skills" would also fire on ordinary
# bullet text such as "Strong IT skills", which is exactly the kind of
# line we want to *collect*, not treat as a new heading.
_HEADING_PHRASES = [
    r"requirements",
    r"what you.?ll need",
    r"what we.?re looking for",
    r"about you",
    r"you have",
    r"qualifications",
    r"skills",
    r"must haves?",
    r"who you are",
    r"essential( criteria| skills)?",
    r"desirable( criteria| skills)?",
]
# Allow a few trailing filler words after the core phrase, e.g. "Skills
# required" or "About you:", but the line can't have much else in it.
_HEADING_RE = re.compile(
    rf"^(?:{'|'.join(_HEADING_PHRASES)})(\s+\w+){{0,2}}$", re.IGNORECASE
)

# Used for the no-headings-found fallback: a line mentioning any of these
# words is probably describing a requirement, even without a heading above it.
_FALLBACK_WORDS_RE = re.compile(
    r"\bexperience\b|\bdegree\b|\bknowledge of\b|\bproficient\b", re.IGNORECASE
)

MAX_REQUIREMENTS = 12
_MAX_HEADING_WORDS = 8       # headings are short; a long line is body text, not a heading
_MAX_REQUIREMENT_LINE_LEN = 220  # drop obviously-not-a-bullet giant paragraphs


def extract_requirements(description_html: str) -> list[str]:
    """
    Find requirements-style bullet points in a job description.

    Strategy: look for short heading lines (see _HEADING_RE) and collect
    the lines under each one until the next heading. If no headings are
    found anywhere, fall back to picking out lines that mention words
    like "experience" or "degree" — that catches descriptions that list
    requirements as plain bullets with no heading at all.
    """
    lines = _html_to_lines(description_html)

    requirements: list[str] = []
    under_heading = False
    for line in lines:
        if _is_heading(line):
            # A requirements-style heading (e.g. "Essential", "Desirable")
            # — start (or keep) collecting.
            under_heading = True
            continue
        if under_heading and _is_unrelated_heading(line):
            # We've hit some *other* heading (e.g. "Benefits") that wasn't
            # in our keyword list — stop collecting until/unless we see
            # another requirements-style heading.
            under_heading = False
            continue
        if under_heading:
            item = _clean_requirement_line(line)
            if item:
                requirements.append(item)
        if len(requirements) >= MAX_REQUIREMENTS:
            break

    if not requirements:
        for line in lines:
            if _FALLBACK_WORDS_RE.search(line):
                item = _clean_requirement_line(line)
                if item:
                    requirements.append(item)
            if len(requirements) >= MAX_REQUIREMENTS:
                break

    # De-duplicate while preserving order (a heading can repeat, e.g.
    # "Essential" and "Desirable" both matching, pulling in the same line twice).
    seen = set()
    deduped = []
    for item in requirements:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped[:MAX_REQUIREMENTS]


def _is_heading(line: str) -> bool:
    stripped = line.strip().rstrip(":").strip()
    if not stripped or len(stripped.split()) > _MAX_HEADING_WORDS:
        return False
    return bool(_HEADING_RE.match(stripped))


def _is_unrelated_heading(line: str) -> bool:
    """
    A short line ending in ':' usually was a bold heading in the original
    HTML (see _html_to_lines) — even if it's not one of our specific
    requirements-style phrases (e.g. "Benefits at RED:"). Used to know when
    a requirements section has ended.
    """
    stripped = line.strip()
    if not stripped.endswith(":"):
        return False
    return len(stripped.rstrip(":").split()) <= _MAX_HEADING_WORDS


def _clean_requirement_line(line: str) -> Optional[str]:
    item = _BULLET_PREFIX_RE.sub("", line).strip()
    if not item or len(item) > _MAX_REQUIREMENT_LINE_LEN:
        return None
    return item


# ---------------------------------------------------------------------------
# Salary
# ---------------------------------------------------------------------------


@dataclass
class SalaryInfo:
    min: Optional[float]
    max: Optional[float]
    text: str


# Matches things like "£30k-£35k", "£30,000 to £35,000", "£45000-£50000 per annum".
_SALARY_RANGE_RE = re.compile(
    r"£\s?([\d,]+(?:\.\d+)?)\s?([kK])?\s*(?:-|–|—|to)\s*£?\s?([\d,]+(?:\.\d+)?)\s?([kK])?",
)
# Matches a single amount, e.g. "£35,000 per annum" or "£40k pro rata".
_SALARY_SINGLE_RE = re.compile(r"£\s?([\d,]+(?:\.\d+)?)\s?([kK])?")


def _to_amount(number_str: str, k_suffix: Optional[str]) -> float:
    amount = float(number_str.replace(",", ""))
    return amount * 1000 if k_suffix else amount


def extract_salary(text: str) -> Optional[SalaryInfo]:
    """
    Parse a £ salary (or salary range) out of free text, for sources (like
    Adzuna, when it doesn't supply structured salary fields) that only give
    us a description to search. Returns None if nothing that looks like a
    salary is found.
    """
    if not text:
        return None

    range_match = _SALARY_RANGE_RE.search(text)
    if range_match:
        low_str, low_k, high_str, high_k = range_match.groups()
        low = _to_amount(low_str, low_k)
        high = _to_amount(high_str, high_k)
        # A range is written smallest-first ("£30k-£35k"), so no need to swap.
        return SalaryInfo(min=low, max=high, text=range_match.group(0).strip())

    single_match = _SALARY_SINGLE_RE.search(text)
    if single_match:
        amount_str, k_suffix = single_match.groups()
        amount = _to_amount(amount_str, k_suffix)
        return SalaryInfo(min=amount, max=amount, text=single_match.group(0).strip())

    return None


# ---------------------------------------------------------------------------
# Work mode
# ---------------------------------------------------------------------------

# Checked in this order — "hybrid" is the most specific and should win over
# a description that happens to also mention "remote" in passing.
_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
_REMOTE_RE = re.compile(r"\bremote\b|\bwork from home\b|\bwfh\b", re.IGNORECASE)
_ONSITE_RE = re.compile(r"\bon[\s-]?site\b|\boffice[\s-]based\b|\bin[\s-]office\b", re.IGNORECASE)


def detect_work_mode(text: str) -> str:
    """Guess hybrid / remote / onsite / unknown from free text."""
    if not text:
        return "unknown"
    if _HYBRID_RE.search(text):
        return "hybrid"
    if _REMOTE_RE.search(text):
        return "remote"
    if _ONSITE_RE.search(text):
        return "onsite"
    return "unknown"


# ---------------------------------------------------------------------------
# Years of experience required
# ---------------------------------------------------------------------------

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_WORD_NUMBER_PATTERN = "|".join(_WORD_NUMBERS.keys())

# Checked in order; each pattern's first captured group is the *minimum*
# years required by that phrasing (for a range like "2-4 years", that's the
# lower number — matching the spec's "for a range, use the lower number").
_YEARS_PATTERNS = [
    re.compile(r"(\d+)\s*\+\s*years?", re.IGNORECASE),
    re.compile(r"(\d+)\s*-\s*\d+\s*years?", re.IGNORECASE),
    re.compile(r"at least\s+(\d+)\s*years?", re.IGNORECASE),
    re.compile(r"minimum\s+(?:of\s+)?(\d+)\s*years?", re.IGNORECASE),
    re.compile(r"(\d+)\s*yrs?\b", re.IGNORECASE),
    re.compile(rf"({_WORD_NUMBER_PATTERN})\s*years?", re.IGNORECASE),
]


def extract_years_required(text: str) -> Optional[int]:
    """
    Find the minimum years of experience a job description asks for, e.g.
    matching "3+ years", "2-4 years" (-> 2), "at least 5 years", "minimum
    of 6 years", "five years", "5 yrs". Returns None if no such phrase is
    found (§6.1 treats that as "any experience level is fine").
    """
    if not text:
        return None
    for pattern in _YEARS_PATTERNS:
        match = pattern.search(text)
        if match:
            value = match.group(1).lower()
            return _WORD_NUMBERS.get(value, None) if value in _WORD_NUMBERS else int(value)
    return None
