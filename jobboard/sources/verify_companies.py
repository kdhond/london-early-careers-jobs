"""
Standalone check: does each company in config/companies.yaml still have a
working career-page API?

Companies rename or shut down their boards from time to time, and this app
should never silently show stale/broken entries. Run this whenever you add
a new company, or every so often as a health check:

    python -m jobboard.sources.verify_companies

It hits every company's board directly (the same URL each source module
uses) and prints OK/FAIL for each one, so you know which entries in
companies.yaml are safe to keep and which need fixing or removing.
"""
from __future__ import annotations

import sys

import httpx

from jobboard.sources.base import USER_AGENT, load_yaml

HEADERS = {"User-Agent": USER_AGENT}


def check_one(company: dict) -> tuple[str, bool, str]:
    """Returns (company name, ok?, message)."""
    name = company["name"]
    ats = company.get("ats")
    token = company.get("token")

    try:
        with httpx.Client(headers=HEADERS, timeout=15) as client:
            if ats == "greenhouse":
                resp = client.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
                if resp.status_code == 200:
                    n = len(resp.json().get("jobs", []))
                    return (name, n > 0, f"{n} jobs")
                return (name, False, f"HTTP {resp.status_code}")

            if ats == "lever":
                # Same fallback logic as lever.py: try the default instance,
                # then the EU one, before giving up.
                for base in (
                    "https://api.lever.co/v0/postings",
                    "https://api.eu.lever.co/v0/postings",
                ):
                    resp = client.get(f"{base}/{token}?mode=json")
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            return (name, True, f"{len(data)} jobs")
                return (name, False, "no postings on either lever instance")

            if ats == "ashby":
                resp = client.get(
                    f"https://api.ashbyhq.com/posting-api/job-board/{token}"
                    "?includeCompensation=true"
                )
                if resp.status_code == 200:
                    n = len(resp.json().get("jobs", []))
                    return (name, n > 0, f"{n} jobs")
                return (name, False, f"HTTP {resp.status_code}")

            # The four extra ATS platforms (see more_ats.py): each has one
            # list URL, and "ok" means it answered 200 with at least one job.
            more = {
                "workable": (f"https://apply.workable.com/api/v1/widget/accounts/{token}",
                             lambda d: len(d.get("jobs", []))),
                "recruitee": (f"https://{token}.recruitee.com/api/offers/",
                              lambda d: len(d.get("offers", []))),
                "smartrecruiters": (f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=1",
                                    lambda d: d.get("totalFound", 0)),
            }
            if ats in more:
                url, count = more[ats]
                resp = client.get(url)
                if resp.status_code == 200:
                    n = count(resp.json())
                    return (name, n > 0, f"{n} jobs")
                return (name, False, f"HTTP {resp.status_code}")

            if ats == "bamboohr":
                resp = client.get(f"https://{token}.bamboohr.com/careers/list")
                if resp.status_code == 200:
                    n = len(resp.json().get("result", []))
                    return (name, n > 0, f"{n} jobs")
                return (name, False, f"HTTP {resp.status_code}")

            if ats == "teamtailor":
                resp = client.get(f"https://{token}.teamtailor.com/jobs.rss")
                if resp.status_code == 200:
                    n = resp.text.count("<item>")
                    return (name, n > 0, f"{n} jobs")
                return (name, False, f"HTTP {resp.status_code}")

            if ats == "workday":
                resp = client.post(
                    f"https://{token}.{company['wd']}.myworkdayjobs.com/wday/cxs/{token}/{company['site']}/jobs",
                    json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": company.get("search", "London")},
                )
                if resp.status_code == 200:
                    n = resp.json().get("total", 0)
                    return (name, n > 0, f"{n} London-matching jobs")
                return (name, False, f"HTTP {resp.status_code}")

            if ats == "personio":
                resp = client.get(f"https://{token}.jobs.personio.de/xml")
                if resp.status_code == 200:
                    n = resp.text.count("<position>")
                    return (name, n > 0, f"{n} jobs")
                return (name, False, f"HTTP {resp.status_code}")

            return (name, False, f"unknown ats '{ats}'")
    except Exception as exc:
        return (name, False, f"error: {exc}")


def main() -> int:
    companies = load_yaml("companies.yaml") or []
    failures = []
    for company in companies:
        name, ok, message = check_one(company)
        status = "OK  " if ok else "FAIL"
        print(f"{status} {name:30} ({company.get('ats')}/{company.get('token')})  {message}")
        if not ok:
            failures.append(name)

    print()
    print(f"{len(companies) - len(failures)}/{len(companies)} companies verified OK")
    if failures:
        print("Failing companies (consider fixing the token or removing the entry):")
        for name in failures:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
