"""
Generic reader for a company's OWN careers page (no ATS feed behind it).

Most small UK biotechs don't use Greenhouse/Lever/etc.; they list roles on
their own website. There's no API, so this source reads the page itself:

  1. Fetch the listing page (`careers_url` in config/companies.yaml).
  2. Collect links that look like individual job postings: they sit deeper in
     the URL than the listing page and contain a job-ish path segment such as
     /job/, /vacancy/, /position/, /roles/.
  3. Open each posting (capped) and read it. A schema.org JobPosting block
     (JSON-LD) is used when the page has one, since it gives exact title,
     location and date; otherwise we fall back to the page's <h1>/<title>
     and body text, and only accept the page if it reads like a job ad.

This is heuristic by nature: pages rendered purely by JavaScript yield nothing
(static HTML only), and a redesign can break a company. A page that fails is
skipped, never fatal, same as every other source.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import time
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import USER_AGENT, request_with_retry
from jobboard.sources.more_ats import _CompanyATSSource, _html_to_text, _join_location

MAX_POSTINGS_PER_COMPANY = 25

# A link counts as a posting candidate only if its path has one of these.
_JOB_PATH_RE = re.compile(
    r"/(?:jobs?|vacanc(?:y|ies)|positions?|roles?|openings?|opportunit(?:y|ies)|careers?|join-us|working-with-us)/[^/?#]+",
    re.IGNORECASE,
)
# Link text that is navigation, not a job title.
_NAV_TEXT_RE = re.compile(
    r"^(?:apply|apply now|view all|see all|all jobs|all roles|open roles|careers?|jobs|learn more|read more|"
    r"join us|join our team|back|home|search|benefits|culture|our values|life at .*|contact.*|about.*|"
    r"privacy.*|cookie.*|terms.*|login|sign in|register|view more|more|next|previous|submit.*|"
    r"see open roles|view open roles|view jobs|view roles|current vacancies|vacancies)$",
    re.IGNORECASE,
)
# Phrases that make a page read like a real job ad (used when there's no JSON-LD).
_JOB_AD_RE = re.compile(
    r"responsibilit|requirements|about the role|what you.ll|who you are|apply for this|"
    r"closing date|key duties|person specification|qualifications",
    re.IGNORECASE,
)
# Headings of non-job pages that slip through (culture, benefits, hiring process...).
_NOT_A_TITLE_RE = re.compile(
    r"\b(?:culture|diversity|inclusion|belonging|benefits|rewards?|journey|why (?:join|work)|working at|"
    r"our (?:people|values|story|mission)|how we hire|well-?being|life at|career (?:areas|development|progression)|"
    r"vacancies|opportunities|featured|experienced professionals)\b|^careers?$",
    re.IGNORECASE,
)
_LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
_A_RE = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_UK_RE = re.compile(r"\b(?:london|united kingdom|uk|england|cambridge|oxford|stevenage|manchester|reading|slough|harlow|abingdon|didcot|welwyn|hertfordshire|surrey)\b", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _html_to_text(text or "")).strip()


def _json_ld_postings(page: str) -> list[dict]:
    """Every schema.org JobPosting object in the page's JSON-LD blocks."""
    found: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" in types:
                found.append(node)
            for v in node.values():
                if isinstance(v, (list, dict)):
                    walk(v)

    for block in _LD_RE.findall(page):
        try:
            walk(json.loads(block.strip()))
        except ValueError:
            continue
    return found


def _ld_location(posting: dict) -> str:
    """Flatten JobPosting.jobLocation into 'City, Region, Country'."""
    locs = posting.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    parts = []
    for loc in locs:
        addr = (loc or {}).get("address", {}) if isinstance(loc, dict) else {}
        if isinstance(addr, str):
            parts.append(addr)
            continue
        country = addr.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        parts.append(_join_location(addr.get("addressLocality"), addr.get("addressRegion"), country))
    if posting.get("jobLocationType") == "TELECOMMUTE":
        parts.append("Remote")
    return "; ".join(p for p in parts if p)


class CareersPageSource(_CompanyATSSource):
    name = "careers"

    def _fetch_company(self, client: httpx.Client, company: dict) -> list[Job]:
        listing_url = company["careers_url"]
        resp = request_with_retry(client, "GET", listing_url, follow_redirects=True)
        if resp.status_code != 200:
            return []
        listing_html = resp.text

        # Some listing pages embed the postings' JSON-LD directly.
        out = [self._from_ld(company, p, listing_url) for p in _json_ld_postings(listing_html)]
        if out:
            return out

        out = []
        for url, link_text in self._candidate_links(listing_html, listing_url):
            time.sleep(0.3)  # be polite to small company sites
            try:
                r = request_with_retry(client, "GET", url, follow_redirects=True)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            job = self._from_posting_page(company, url, r.text, link_text)
            if job:
                out.append(job)
        return out

    def _candidate_links(self, page: str, listing_url: str) -> list[tuple[str, str]]:
        """Links on the listing page that look like individual job postings."""
        listing_path = urlparse(listing_url).path.rstrip("/")
        seen: set[str] = set()
        out = []
        for href, inner in _A_RE.findall(page):
            url, _ = urldefrag(urljoin(listing_url, htmllib.unescape(href)))
            parsed = urlparse(url)
            text = _clean(inner)
            if url in seen or url.rstrip("/") == listing_url.rstrip("/"):
                continue
            if parsed.scheme not in ("http", "https") or not (6 <= len(text) <= 110):
                continue
            if _NAV_TEXT_RE.match(text) or not _JOB_PATH_RE.search(parsed.path):
                continue
            # Must be deeper than the listing page itself (a sibling like
            # /careers/benefits is still caught by the ad-text check later).
            if parsed.path.rstrip("/") == listing_path:
                continue
            seen.add(url)
            out.append((url, text))
            if len(out) >= MAX_POSTINGS_PER_COMPANY:
                break
        return out

    def _from_ld(self, company: dict, posting: dict, page_url: str) -> Job:
        description_html = htmllib.unescape(posting.get("description") or "")
        location = _ld_location(posting)
        return self._job(
            company,
            title=_clean(posting.get("title") or ""),
            location=location,
            posted_at=(posting.get("datePosted") or "")[:10] or None,
            description_html=description_html,
            description_text=_clean(description_html),
            apply_links=[ApplyLink(source=self.name, url=posting.get("url") or page_url)],
        )

    def _from_posting_page(self, company: dict, url: str, page: str, link_text: str) -> Job | None:
        postings = _json_ld_postings(page)
        if postings:
            return self._from_ld(company, postings[0], url)

        body = _clean(page)
        # Marketing pages ("Our culture", "Benefits") also mention one of these
        # phrases, so insist on two distinct ones plus a real amount of text.
        ad_phrases = {m.group(0).lower() for m in _JOB_AD_RE.finditer(body)}
        if len(ad_phrases) < 2 or len(body) < 800:
            return None  # a generic page, not a job ad
        h1 = _H1_RE.search(page)
        title = _clean(h1.group(1)) if h1 else link_text
        if not (4 <= len(title) <= 120):
            title = link_text
        if _NOT_A_TITLE_RE.search(title):
            return None
        # Location: only trust a UK place named near the top of the ad. We do
        # NOT fall back to the company's home country here (unlike the ATS
        # sources) because free-text pages often belong to multinationals —
        # a "Skokie" or "Burnaby" job must not be labelled United Kingdom.
        m = _UK_RE.search(body[:1500])
        location = m.group(0) if m else ""
        return self._job(
            company,
            title=title,
            location=location,
            description_html="",
            description_text=body[:6000],
            apply_links=[ApplyLink(source=self.name, url=url)],
        )
