"""
Two specialist job boards that list the research / health / charity roles the
big aggregators bury: jobs.ac.uk (universities, institutes, research) and
CharityJob (charities, health-equity and public-health organisations).

Neither offers an API, so these read the public search-results HTML (both
sites' robots.txt allow it). Each search is a handful of requests for the
first page or two of results, one search per keyword in
`bio_search_keywords` (config/settings.yaml), so the load is small and
polite. A board that changes its markup yields zero jobs rather than
crashing the refresh (see Source.safe_fetch).
"""
from __future__ import annotations

import html
import re
import time
from datetime import date, datetime
from urllib.parse import quote_plus, urljoin

import httpx

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import Source, load_yaml, request_with_retry

_BROWSER_UA = "Mozilla/5.0 (compatible; london-early-careers-jobs/1.0)"
_TAG_RE = re.compile(r"<[^>]+>")

# Academic seniority that early-career filters.py does not catch by title word.
_TOO_SENIOR_RE = re.compile(
    r"\b(?:professor(?:ship)?|chair|reader|lecturer|head of|director|principal|senior)\b", re.IGNORECASE
)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", fragment or ""))).strip()


def _day_month_to_iso(value: str) -> str | None:
    """'29 Sep' -> this year's (or last year's, if that'd be in the future) ISO date."""
    try:
        parsed = datetime.strptime(value.strip() + f" {date.today().year}", "%d %b %Y").date()
    except ValueError:
        return None
    if parsed > date.today():
        parsed = parsed.replace(year=parsed.year - 1)
    return parsed.isoformat()


class JobsAcUkSource(Source):
    name = "jobsacuk"
    PAGES_PER_KEYWORD = 2   # 25 results per page, newest first

    def __init__(self):
        self.keywords: list[str] = load_yaml("settings.yaml").get("bio_search_keywords", [])

    def is_configured(self) -> bool:
        return bool(self.keywords)

    def fetch(self) -> list[Job]:
        jobs: dict[str, Job] = {}
        with httpx.Client(headers={"User-Agent": _BROWSER_UA}, follow_redirects=True) as client:
            for keyword in self.keywords:
                for page in range(self.PAGES_PER_KEYWORD):
                    url = (
                        "https://www.jobs.ac.uk/search/?keywords=" + quote_plus(keyword)
                        + "&sortOrder=1&pageSize=25&startIndex=" + str(1 + 25 * page)
                    )
                    resp = request_with_retry(client, "GET", url)
                    time.sleep(0.5)
                    if resp.status_code != 200:
                        break
                    found = self._parse(resp.text)
                    for job in found:
                        jobs.setdefault(job.id, job)
                    if len(found) < 25:
                        break
        return list(jobs.values())

    def _parse(self, page: str) -> list[Job]:
        out = []
        # Each result starts at its j-search-result__result container.
        for block in re.split(r'<div class="j-search-result__result', page)[1:]:
            link = re.search(r'<a href="(/job/[^"]+)">(.*?)</a>', block, re.DOTALL)
            if not link:
                continue
            title = _text(link.group(2))
            if _TOO_SENIOR_RE.search(title):
                continue
            employer = re.search(r'j-search-result__employer">(.*?)</div>', block, re.DOTALL)
            location = re.search(r"Location:\s*(.*?)</div>", block, re.DOTALL)
            placed = re.search(r"Date Placed:\s*</strong>\s*([0-9]{1,2} [A-Za-z]{3})", block)
            salary = re.search(r"Salary:\s*</strong>(.*?)</div>", block, re.DOTALL)
            info = _text(block[: block.find("j-search-result__date-logos")]) if "j-search-result__date-logos" in block else ""
            out.append(Job.new(
                title=title,
                company=_text(employer.group(1)) if employer else "",
                location=_text(location.group(1)) if location else "",
                source=self.name,
                posted_at=_day_month_to_iso(placed.group(1)) if placed else None,
                salary_text=_text(salary.group(1))[:120] if salary else None,
                description_html="",
                description_text=info,
                description_is_full=False,
                apply_links=[ApplyLink(source=self.name, url=urljoin("https://www.jobs.ac.uk", link.group(1)))],
            ))
        return out


class CharityJobSource(Source):
    name = "charityjob"

    def __init__(self):
        self.keywords: list[str] = load_yaml("settings.yaml").get("charity_search_keywords", [])

    def is_configured(self) -> bool:
        return bool(self.keywords)

    def fetch(self) -> list[Job]:
        jobs: dict[str, Job] = {}
        with httpx.Client(headers={"User-Agent": _BROWSER_UA}, follow_redirects=True) as client:
            for keyword in self.keywords:
                url = f"https://www.charityjob.co.uk/jobs?keywords={quote_plus(keyword)}&location=london"
                resp = request_with_retry(client, "GET", url)
                time.sleep(0.5)
                if resp.status_code != 200:
                    continue
                for job in self._parse(resp.text):
                    jobs.setdefault(job.id, job)
        return list(jobs.values())

    def _parse(self, page: str) -> list[Job]:
        out = []
        for block in re.split(r'<div class="job-card"', page)[1:]:
            title_link = re.search(r'<a href="(https://www\.charityjob\.co\.uk/jobs/[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            org = re.search(r'class="organisation">(.*?)</div>', block, re.DOTALL)
            if not title_link or not org:
                continue
            title = _text(title_link.group(2))
            if _TOO_SENIOR_RE.search(title):
                continue
            # "Dementia UK, London (Hybrid)" -> employer, location.
            employer, _, location = _text(org.group(1)).partition(",")
            salary = re.search(r"icon-pound\"></i><span>(.*?)</span>", block, re.DOTALL)
            blurb = re.search(r'job-summary-item">\s*([^<]{40,})', block)
            out.append(Job.new(
                title=title,
                company=employer.strip(),
                location=location.strip() or "United Kingdom",
                source=self.name,
                salary_text=_text(salary.group(1)) if salary else None,
                description_html="",
                description_text=_text(blurb.group(1)) if blurb else "",
                description_is_full=False,
                apply_links=[ApplyLink(source=self.name, url=title_link.group(1))],
            ))
        return out
