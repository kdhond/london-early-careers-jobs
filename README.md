# London Early-Careers Jobs

A local job board for early-career roles (0–4 years' experience) in London
and England. It pulls jobs from several sources, removes duplicates,
groups them by category, and shows the full description, requirements,
and links to apply — all in your browser, running entirely on your own
machine.

Modelled loosely on [newgrad-jobs.com](https://www.newgrad-jobs.com/?k=hc).

> A screenshot would go here — add one once you've run it locally and want
> to drop `screenshot.png` into the repo.

There's also a **public, static version** of this board, rebuilt once a
day by a free GitHub Actions robot and served free by GitHub Pages — see
[Public site](#public-site-github-pages-daily-refresh) below. It's the same
data and the same frontend, just read-only (no Refresh button) and
world-visible instead of local-only.

## What it does

- Aggregates jobs from **LinkedIn** (via Apify, never scraped directly —
  see below), **Reed**, **Adzuna**, and ~180 company career pages
  (Greenhouse / Lever / Ashby boards).
- Filters to **0–4 years' experience** and **England-only (or UK-remote)**
  locations.
- **Deduplicates** the same job seen across multiple sources into one
  listing with every apply link attached.
- **Two independent classifications on every job**: a general **type**
  category anyone can filter by (Software Engineering, AI/ML, Data &
  Analytics, Finance, Consulting, and 12 more — `config/categories.yaml`),
  and a personal **★ relevant** flag for the author's own biotech/VC/
  health-equity/finance job search (`config/relevance.yaml`) — one doesn't
  replace the other; see [Two categorisation systems](#two-categorisation-systems-type-vs-relevance) below.
- Shows the **full description**, **extracted requirements**, salary (when
  known), work mode, posting date, and the **company's real website**
  (never a guessed URL).
- Has a **Refresh** button that re-fetches everything, with a live
  per-source progress panel (local version only — the public site refreshes
  itself once a day instead).
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

## Public site (GitHub Pages, daily refresh)

The public version reuses the exact same pipeline (`jobboard/refresh.py`)
and the exact same `data/jobs.db` — nothing about fetching, filtering,
deduping or categorising is different. The only new piece is
`jobboard/export_static.py`, which reads the active jobs back out and
writes them to `docs/data/jobs.json`, a single static file. `docs/index.html`
is a separate copy of the frontend that fetches that JSON file instead of
a live `/api/jobs` — there is no server behind the public site at all.
`.github/workflows/refresh.yml` runs both steps once a day
(`0 6 * * *`, 06:00 UTC) via GitHub Actions, which is free for public repos.

To set this up on your own fork:

1. **Make the repo public** (Settings → General → Danger Zone) — GitHub
   Pages' free tier requires it.
2. **Add your API keys as Actions secrets**, not `.env` (Settings → Secrets
   and variables → Actions): `REED_API_KEY`, `ADZUNA_APP_ID`,
   `ADZUNA_APP_KEY`, `APIFY_TOKEN` — same names as `.env`, just stored on
   GitHub instead, since `.env` itself is never committed.
3. **Enable Pages** (Settings → Pages): source = `main` branch, `/docs`
   folder.
4. Trigger the workflow once manually (Actions tab → "Daily job refresh" →
   "Run workflow") rather than waiting for the schedule, to confirm it
   works end-to-end.

**Why `data/jobs.db` persists via `actions/cache`, not a git commit**:
GitHub Actions runners are thrown away after every run, so *something* has
to carry `data/jobs.db` forward — without it, every day would start from a
completely empty history: no "new today" counts, no inactive-job tracking,
and critically, no memory of this month's Apify spend, which would risk
the $4/month cap silently not applying. The database is genuinely large
(~40MB) and grows over time, so committing it to git the way `docs/data/jobs.json`
is committed would add that much to git history *every single day,
forever* — roughly 14GB/year. `actions/cache` (see `.github/workflows/refresh.yml`)
gets the same continuity without any of that: each run restores the most
recent cached copy, updates it, and saves a fresh one, entirely outside
git history.

`docs/data/jobs.json` itself (~25MB) still has to be committed — GitHub
Pages serves straight from the repo, so there's no way around that one.
Repo size will grow by roughly that much per day (a private benefit of
Actions/git being free either way, but worth knowing) — if that ever
becomes unwieldy, periodically squashing git history is a reasonable fix,
but not needed to get started.

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

## Two categorisation systems: type vs. relevance

Every job gets tagged by two completely independent systems that answer
different questions:

- **`category`** (`config/categories.yaml`, `jobboard/categorise.py`) — a
  general job-TYPE taxonomy (Software Engineering, Data & Analytics,
  Finance & Accounting, Consulting & Strategy, etc.), shown as the tabs
  across the top. This is what makes the board useful to *anyone*, not
  just the author.
- **`is_relevant`** (`config/relevance.yaml`, `jobboard/relevance.py`) — a
  boolean flag for the author's own biotech/VC/health-equity/finance job
  search, shown as a ★ next to the title and an opt-in filter toggle. A
  job can be "Software Engineering" and not relevant, or "Healthcare &
  Science" and relevant, or neither, or both — the two are unrelated.

Both are ordered keyword-rule lists checked **first match wins** (put more
specific categories above more general ones), matched as whole words/
phrases (case-insensitive). `categories.yaml` falls back to matching the
full description if nothing matched the title; `relevance.yaml` is
deliberately **title-only** (see its file header for why — free-text
descriptions are full of unrelated boilerplate that happens to contain a
keyword).

Note: `AI / Machine Learning` is deliberately listed before
`Data & Analytics` in `categories.yaml` — otherwise "Data Scientist" roles
would be caught by Data & Analytics' own keywords before AI/ML ever got a
chance to match.

`relevance.yaml`'s company-hint fallback (any job at one of the verified
biotech/VC/health-equity companies in `companies.yaml` counts as relevant
even without a keyword match) explicitly excludes software/hardware
engineering titles — see `jobboard/relevance.py`'s `_ENGINEERING_ROLE_RE`.

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
app.py                    Flask server, routes, refresh orchestration (local dev/preview only)
jobboard/
  models.py                Job dataclass (the normalised schema every source maps to)
  db.py                    SQLite schema, upserts, queries
  refresh.py                Runs the whole pipeline: fetch -> normalise -> extract -> filter -> dedupe -> categorise -> save
  export_static.py           Reads active jobs -> docs/data/jobs.json, for the public site
  budget.py                 Apify cooldown + monthly spending cap
  extract.py                 Requirements/salary/years/work-mode extraction from descriptions
  filters.py                  0-4 years + England-only (or UK-remote) filters
  dedupe.py                    Fuzzy duplicate detection and merging
  categorise.py                 General job-TYPE category assignment (public, everyone)
  relevance.py                   Personal biotech/VC/health-equity/finance flag (author only)
  sources/
    base.py                      Shared Source interface, HTTP retry helper, YAML loader
    greenhouse.py, lever.py, ashby.py    Company career-page sources
    verify_companies.py                    Health-check for config/companies.yaml
    reed.py, adzuna.py, linkedin_apify.py  The three paid/keyed sources
config/
  settings.yaml               LinkedIn search keywords, locations, thresholds, budget
  companies.yaml               ~180 verified companies
  categories.yaml               General job-TYPE keyword rules
  relevance.yaml                 Personal relevance keyword rules
static/index.html            Local-dev frontend — talks to the live Flask API
docs/index.html               Public-site frontend — talks to the static docs/data/jobs.json
.github/workflows/refresh.yml  Daily GitHub Actions job (refresh + export + commit)
tests/                        pytest suite for extract/filters/dedupe/categorise/relevance
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
