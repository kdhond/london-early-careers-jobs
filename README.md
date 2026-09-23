# London Early-Careers Jobs

A local job board for early-career roles (0–4 years' experience) in London
and England. It pulls jobs from several sources, removes duplicates,
groups them by category, and shows the full description, requirements,
and links to apply — all in your browser, running entirely on your own
machine.

Modelled loosely on [newgrad-jobs.com](https://www.newgrad-jobs.com/?k=hc).

> A screenshot would go here — add one once you've run it locally and want
> to drop `screenshot.png` into the repo.

## What it does

- Aggregates jobs from **LinkedIn** (via Apify, never scraped directly —
  see below), **Reed**, **Adzuna**, and ~150 company career pages
  (Greenhouse / Lever / Ashby boards).
- Filters to **0–4 years' experience** and **England-only** locations.
- **Deduplicates** the same job seen across multiple sources into one
  listing with every apply link attached.
- **Categorises** every job (Software Engineering, AI/ML, Data & Analytics,
  Finance, Consulting, and 13 more) using keyword rules you can edit.
- Shows the **full description**, **extracted requirements**, salary (when
  known), work mode, and posting date.
- Has a **Refresh** button that re-fetches everything, with a live
  per-source progress panel.
- **Never risks getting your IP banned** — there is no direct scraping of
  LinkedIn, Indeed, Glassdoor, etc. LinkedIn data comes only from Apify,
  running on Apify's own servers.
- Runs entirely on your machine, on Apify's **free $5/month credit** — see
  [Apify cost & budget](#apify-cost--budget) below.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then fill in whichever keys you have — see below
python app.py
```

That starts the server at `http://127.0.0.1:8000` and opens it in your
browser automatically. The server only listens on `127.0.0.1` (localhost)
— it's never reachable from your network.

**It works with zero API keys.** Company career pages (Greenhouse, Lever,
Ashby) need no key at all, so the board is useful from the very first run.
Add keys to `.env` whenever you want — LinkedIn, Reed and Adzuna all
just show up as "not configured" until you do, and start working the next
time you refresh, no restart needed.

## Getting each API key

| Source | Needed? | Cost | Where to get it |
|---|---|---|---|
| Greenhouse / Lever / Ashby | No key | Free | Nothing to do — these are public APIs |
| Reed | `REED_API_KEY` | Free | Sign up at [reed.co.uk/developers](https://www.reed.co.uk/developers/jobseeker) |
| Adzuna | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | Free | Register at [developer.adzuna.com](https://developer.adzuna.com/) |
| LinkedIn (via Apify) | `APIFY_TOKEN` | ~$0.40/1,000 results, covered by Apify's free $5/month credit | Sign up at [apify.com](https://apify.com), copy your token from **Settings → Integrations** |

Paste each key into `.env` (never into any tracked file — `.env` is
gitignored, and nothing in this repo ever hardcodes a key).

## Apify cost & budget

The spec this app was built from assumed a different, since-removed Apify
actor priced at $0.10 per 1,000 results. That actor no longer exists, so
this app uses [`valig/linkedin-jobs-scraper`](https://apify.com/valig/linkedin-jobs-scraper)
instead — picked after comparing it against the alternatives on Apify's
store for the best balance of cost and results volume. Its real free-tier
price is **$0.40 per 1,000 results**, plus a small flat fee every time it
starts (see `jobboard/budget.py` for exactly how that's accounted for).

Two independent safety limits, both in `config/settings.yaml` under
`apify:`, keep this affordable:

- **`cooldown_minutes`** (default 60): LinkedIn won't be re-queried more
  often than this, even if you mash Refresh.
- **`monthly_budget_usd`** (default $4.00, leaving $1 of headroom under
  Apify's $5 free monthly credit): a run is skipped entirely if it would
  push this month's estimated spend over the cap. This check always
  applies — even "Force LinkedIn refresh" can't bypass it, only the
  cooldown.

`max_results_per_run` (default 290) is sized so that **one refresh a day,
every day, comfortably fits inside the $4/month cap** (see the comment in
`config/settings.yaml` for the exact math). If you refresh less often than
daily, you can safely raise it.

The page's header shows current spend ("$1.20 of $4.00 used"), when
LinkedIn last ran, and when it's next allowed to — and tells you why, if
it was skipped.

## Adding a company

Company career pages live in `config/companies.yaml`. Each entry:

```yaml
- name: Monzo
  ats: greenhouse          # greenhouse | lever | ashby
  token: monzo             # the board's slug — check the URL on their careers page
  website: https://monzo.com
  category_hint: fintech    # optional, not currently used by categorise.py
```

To find a company's `token`: visit their careers page and look at the
underlying board URL — Greenhouse boards look like
`boards.greenhouse.io/<token>`, Lever like `jobs.lever.co/<token>`, Ashby
like `jobs.ashbyhq.com/<token>`.

Every entry currently in `companies.yaml` was checked against the live
API before being committed. After adding one, verify it works:

```bash
python -m jobboard.sources.verify_companies
```

This calls every company's board and reports OK/FAIL for each — fix or
remove any that fail (a company may have moved ATS providers, or closed
their board).

## Adding or adjusting a category

Category rules live in `config/categories.yaml`, checked in order —
**first match wins**, so put more specific categories above more general
ones. Each category has a `name` and a list of `keywords`, matched as
whole words/phrases (case-insensitive) against the job title first, then
the full description if nothing matched the title.

Note: `AI / Machine Learning` is deliberately listed before
`Data & Analytics`, even though it reads second in the spec's numbered
list — otherwise "Data Scientist" roles would be caught by Data &
Analytics' own keywords before AI/ML ever got a chance to match.

## Scheduling automatic refreshes with launchd

The refresh pipeline also runs from the command line, independent of the
web server:

```bash
python -m jobboard.refresh                # normal refresh
python -m jobboard.refresh --force-linkedin  # ignore the cooldown (budget cap still applies)
```

To run it automatically once a day, create
`~/Library/LaunchAgents/com.local.london-jobs-refresh.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.local.london-jobs-refresh</string>
    <key>ProgramArguments</key>
    <array>
        <string>/full/path/to/london-early-careers-jobs/.venv/bin/python</string>
        <string>-m</string>
        <string>jobboard.refresh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/full/path/to/london-early-careers-jobs</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>7</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/london-jobs-refresh.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/london-jobs-refresh.log</string>
</dict>
</plist>
```

Then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.local.london-jobs-refresh.plist
```

It'll run every day at 7am, whether or not the web app is open. Adjust the
paths and time to match your setup.

## Project structure

```
app.py                    Flask server, routes, refresh orchestration
jobboard/
  models.py                Job dataclass (the normalised schema every source maps to)
  db.py                    SQLite schema, upserts, queries
  refresh.py                Runs the whole pipeline: fetch -> normalise -> extract -> filter -> dedupe -> categorise -> save
  budget.py                 Apify cooldown + monthly spending cap
  extract.py                 Requirements/salary/years/work-mode extraction from descriptions
  filters.py                  0-4 years + England-only filters
  dedupe.py                    Fuzzy duplicate detection and merging
  categorise.py                 Keyword-rule category assignment
  sources/
    base.py                      Shared Source interface, HTTP retry helper, YAML loader
    greenhouse.py, lever.py, ashby.py    Company career-page sources
    verify_companies.py                    Health-check for config/companies.yaml
    reed.py, adzuna.py, linkedin_apify.py  The three paid/keyed sources
config/
  settings.yaml               Search keywords, locations, thresholds, budget
  companies.yaml               ~150 verified companies
  categories.yaml               Category keyword rules
static/index.html            The whole frontend (vanilla JS/CSS, no build step)
tests/                        pytest suite for extract/filters/dedupe/categorise
```

## Running the tests

```bash
pytest
```

## Known limitations

- **LinkedIn field mapping** was confirmed against one live test run, not
  exhaustively — if Apify or the actor changes its output shape, check
  `jobboard/sources/linkedin_apify.py`'s `_to_job()` against a fresh
  response and adjust.
- **Extraction (requirements/salary/years/work-mode)** is regex-based
  pattern matching over free-text job descriptions written by humans in
  wildly different styles — it's "usually right," not "always right." A
  requirement that's phrased unusually, or a years-of-experience
  expectation implied rather than stated as a number, can slip through.
- **Categorisation** is a single keyword match against title-then-
  description; an incidental one-off mention of a keyword deep in a long
  description can occasionally pull a job into the wrong category.
