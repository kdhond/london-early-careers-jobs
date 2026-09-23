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
