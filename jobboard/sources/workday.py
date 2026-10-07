"""
Workday source.

Most big pharma, CROs and large banks run their careers site on Workday
(`<tenant>.wd<N>.myworkdayjobs.com`) rather than Greenhouse/Lever. Workday has
no official public API, but every one of those careers pages is a front end
for the same unauthenticated JSON endpoints, which is what we call here:

  POST /wday/cxs/<tenant>/<site>/jobs        search + paginate (max 20 per page)
  GET  /wday/cxs/<tenant>/<site><path>       one posting's full details

Unlike the other ATS sources, a Workday company needs THREE values in
companies.yaml, not one:
    token: gsk              the tenant (the subdomain's first part)
    wd:    wd5              the server number in the subdomain
    site:  GSKCareers       the careers-site name (listed in the tenant's
                            https://<tenant>.<wd>.myworkdayjobs.com/robots.txt)

Because these companies hire worldwide, we search each one for the text
"London" rather than downloading every global posting, then fetch each hit's
detail page (the list view often just says "4 Locations", which would hide
the London office from the location filter).
"""
from __future__ import annotations

import concurrent.futures as cf

import httpx

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import request_with_retry
from jobboard.sources.more_ats import _CompanyATSSource, _date_prefix, _html_to_text

PAGE_SIZE = 20          # Workday rejects larger page sizes
MAX_POSTINGS = 200      # per company per refresh — keeps one huge employer from flooding the board
DETAIL_WORKERS = 5      # parallel detail requests per company


class WorkdaySource(_CompanyATSSource):
    name = "workday"

    def _fetch_company(self, client: httpx.Client, company: dict) -> list[Job]:
        tenant, wd, site = company["token"], company["wd"], company["site"]
        host = f"https://{tenant}.{wd}.myworkdayjobs.com"
        api = f"{host}/wday/cxs/{tenant}/{site}"
        # Companies may override the search term (e.g. a non-London office).
        search = company.get("search", "London")

        # --- 1. Page through search results to collect posting paths. ---
        postings: dict[str, dict] = {}   # keyed by path, so repeats collapse
        total = None
        offset = 0
        while len(postings) < MAX_POSTINGS:
            resp = request_with_retry(
                client, "POST", f"{api}/jobs",
                json={"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": search},
            )
            if resp.status_code != 200:
                break
            body = resp.json()
            # Only the first page reports the real total (later pages say 0).
            if total is None:
                total = body.get("total", 0)
            page = body.get("jobPostings", [])
            if not page:
                break
            for p in page:
                if p.get("externalPath"):
                    postings[p["externalPath"]] = p
            offset += PAGE_SIZE
            # Workday wraps around to the start once you page past the end,
            # so stop at the total instead of waiting for an empty page.
            if offset >= total:
                break
        postings = list(postings.values())[:MAX_POSTINGS]

        # --- 2. Fetch each posting's detail page (description + real locations). ---
        def detail(posting: dict) -> Job | None:
            path = posting.get("externalPath")
            if not path:
                return None
            resp = request_with_retry(client, "GET", f"{api}{path}")
            if resp.status_code != 200:
                return None
            info = resp.json().get("jobPostingInfo") or {}
            description_html = info.get("jobDescription", "") or ""
            # `location` is the primary office; `additionalLocations` the rest.
            # Joined so a London office listed second still passes the location filter.
            locations = [info.get("location")] + list(info.get("additionalLocations") or [])
            return self._job(
                company,
                title=info.get("title") or posting.get("title", ""),
                location="; ".join(l for l in locations if l) or posting.get("locationsText", ""),
                employment_type=info.get("timeType"),
                posted_at=_date_prefix(info.get("startDate")),
                description_html=description_html,
                description_text=_html_to_text(description_html),
                apply_links=[ApplyLink(
                    source=self.name,
                    url=info.get("externalUrl") or f"{host}/en-US/{site}{path}",
                )],
            )

        def safe_detail(posting: dict) -> Job | None:
            try:
                return detail(posting)
            except Exception:
                return None  # one bad posting must not lose the rest

        with cf.ThreadPoolExecutor(DETAIL_WORKERS) as pool:
            return [job for job in pool.map(safe_detail, postings) if job]
