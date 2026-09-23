# London Early-Careers Job Board — Build Spec

> **For Claude Code:** Read this whole file before starting. Build the project described here in the current folder, following the commit plan in §11. If something in this spec turns out to be wrong when you test it (an API field name, an endpoint, a URL parameter), trust what the live API returns, fix the code, and note the change in the README. Ask me before doing anything that costs money, or before creating the GitHub repo.

---

## 1. Goal

A Python app that runs on my Mac and shows a **job board for early-career roles (0–4 years' experience) in London and England**, modelled on https://www.newgrad-jobs.com/?k=hc.

- It pulls jobs from several sources, **removes duplicates**, and groups them by **category**.
- For each job it shows the **date posted**, the **full description**, the **requirements** (pulled out of the description), and links to the **company website** and the apply page.
- The page has a **Refresh** button that re-fetches everything.
- As many jobs as possible, **without duplicates**.
- It **must never risk getting my IP banned**. There is no direct scraping of LinkedIn, Indeed, Glassdoor, etc. from my machine. LinkedIn data comes only through Apify, which runs on Apify's servers.
- It costs nothing to run. Apify is used within its free plan ($5 of credit a month).
- The code lives in a **properly named GitHub repo** with a clean commit history.

Out of scope: visa sponsorship, user accounts, hosting it publicly.

---

## 2. Tech stack

- **Python 3.11+**
- **Flask** for the local server and API
- **httpx** for HTTP calls, used concurrently with a thread pool or asyncio (keep it simple)
- **SQLite** (standard library `sqlite3`) for storage
- **rapidfuzz** for fuzzy duplicate matching
- **python-dotenv** for API keys, **PyYAML** for config
- **Frontend:** a single `static/index.html` with vanilla JS and CSS. No build step and no framework.
- **Tests:** pytest

Run it with `python app.py`. That starts the server on `http://localhost:8000` and opens the browser automatically.

---

## 3. Project structure

```
london-early-careers-jobs/
├── app.py                    # Flask server, routes, refresh orchestration, opens browser
├── jobboard/
│   ├── __init__.py
│   ├── models.py             # Job dataclass (normalised schema, §5)
│   ├── db.py                 # SQLite schema, upserts, queries, refresh-run log
│   ├── refresh.py            # runs all sources, then extract → filter → dedupe → categorise → save
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py           # Source interface: name, is_configured(), fetch() -> list[Job]
│   │   ├── linkedin_apify.py
│   │   ├── reed.py
│   │   ├── adzuna.py
│   │   ├── greenhouse.py
│   │   ├── lever.py
│   │   └── ashby.py
│   ├── extract.py            # requirements section, years of experience, salary, work mode
│   ├── filters.py            # 0–4 years / seniority filter, location filter
│   ├── dedupe.py
│   ├── categorise.py
│   └── budget.py             # Apify cooldown + monthly spending cap
├── config/
│   ├── settings.yaml         # search keywords, locations, limits, cooldowns, budget
│   ├── companies.yaml        # company career-page boards (Greenhouse, Lever, Ashby)
│   └── categories.yaml       # category → title keyword rules
├── static/
│   └── index.html
├── tests/
│   ├── test_dedupe.py
│   ├── test_categorise.py
│   ├── test_extract.py
│   └── test_filters.py
├── data/                     # jobs.db lives here (gitignored)
├── .env.example
├── .gitignore
├── requirements.txt
├── LICENSE                   # MIT
└── README.md
```

---

## 4. Data sources

Every source must be **optional**. If its API key is missing from `.env`, skip it, log a note, and show it as "not configured" on the page. The app must still work with only the free, no-key sources (Greenhouse, Lever, Ashby).

All sources: use a descriptive User-Agent, a timeout of about 20s, retries with backoff on 429/5xx, and a concurrency limit of about 5. Wrap each source in try/except so **one failing source never breaks a refresh**.

### 4.1 LinkedIn — via Apify (biggest source)

- Apify actor: **`supreme_coder/linkedin-jobs-scraper`** (https://apify.com/supreme_coder/linkedin-jobs-scraper). It costs about $0.10 per 1,000 results, needs no LinkedIn login or cookies, and runs on Apify's servers.
- Run it synchronously:
  `POST https://api.apify.com/v2/acts/supreme_coder~linkedin-jobs-scraper/run-sync-get-dataset-items?token=$APIFY_TOKEN`
  If the run takes longer than the sync timeout, fall back to the async run endpoint and poll it.
- Input: `{"urls": [<LinkedIn public job-search URLs>], "count": <max results>}`. If the actor supports scraping company details (which gives the company website), turn that on. Check the actor's input schema.
- Build the search URLs from `settings.yaml`. Use the public, logged-out URL format:
  `https://www.linkedin.com/jobs/search/?keywords=<kw>&location=London%2C%20England%2C%20United%20Kingdom&f_E=1%2C2%2C3&f_TPR=r604800`
  - `f_E=1,2,3` = Internship, Entry level, Associate
  - `f_TPR=r604800` = posted in the past week. Use `r86400` (past 24 hours) for routine refreshes after the first run.
  - Also run a search for location "England, United Kingdom".
  - Use a list of broad keywords (see §9) so the searches cover all the categories.
- **Before writing the mapping code**, do one small test run (`count: 10`) and look at the real output fields. Then map them to the Job schema. Expected fields include company, title, location, postedAt, description, applyUrl, seniority, and company website/logo, but confirm them.
- **Budget:** see §8. Never start an Apify run that would take the month's estimated spending over the cap.

### 4.2 Reed (free API key)

- Docs: https://www.reed.co.uk/developers/jobseeker. Sign up to get a key.
- Auth: HTTP Basic, with the API key as the username and an empty password.
- Search: `GET https://www.reed.co.uk/api/1.0/search?keywords=<kw>&locationName=London&distanceFromLocation=25&resultsToTake=100&resultsToSkip=<n>`. Page through the results. Also run a search without a location, or with "England", but filter the results to England (§6.2).
- The search returns a **cut-off description** (about 450 characters). For every job **not already in the database**, fetch the full description:
  `GET https://www.reed.co.uk/api/1.0/jobs/<jobId>` (returns the full `jobDescription`, `externalUrl`, `jobUrl`, contract type, and more).
  - Fetch about 5 at a time, with a small delay between batches, and cache the result in the database so each job is only fetched once.
- Posting date comes from the `date` field (DD/MM/YYYY).

### 4.3 Adzuna (free app_id + app_key)

- Docs: https://developer.adzuna.com/
- Search: `GET https://api.adzuna.com/v1/api/jobs/gb/search/<page>?app_id=..&app_key=..&what=<kw>&where=London&results_per_page=50&max_days_old=14&content-type=application/json`
- The API has **no full-description endpoint**. Keep the snippet and set `description_is_full = False`. The page shows "View full listing" for these jobs.
- Respect Adzuna's free-tier rate limits. Keep the number of calls modest and make the total configurable in `settings.yaml`.

### 4.4 Company career pages (free, no keys)

These are official public job-board APIs. They return full descriptions and the jobs are always up to date.

- **Greenhouse:** `GET https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true`
  The `content` field is HTML-escaped, so unescape it and then strip the HTML.
- **Lever:** `GET https://api.lever.co/v0/postings/<company>?mode=json`
  Some companies are on the EU instance, `https://api.eu.lever.co/v0/postings/<company>?mode=json`. Try both.
- **Ashby:** `GET https://api.ashbyhq.com/posting-api/job-board/<name>?includeCompensation=true`

Keep only jobs located in London or England (§6.2), then apply the experience filter (§6.1).

**`config/companies.yaml`**: build a starting list of **about 150 companies that hire in London**, across tech, fintech, AI, consulting, finance, media and consumer. Examples of the kinds of companies to include (you must check each one): Monzo, Revolut, Wise, Starling, GoCardless, Deliveroo, Cleo, Palantir, DeepMind, Synthesia, ElevenLabs, Wayve, Faculty, Improbable, Onfido, Checkout.com, Zopa, Octopus Energy, Ocado Technology, Skyscanner, Depop, Marshmallow, PhysicsX, Tide, TrueLayer, Paddle, Multiverse, Babylon-style health-tech, and so on. For each company, record:

```yaml
- name: Monzo
  ats: greenhouse          # greenhouse | lever | ashby
  token: monzo             # board token / slug
  website: https://monzo.com
  category_hint: fintech   # optional
```

Write a small script, `python -m jobboard.sources.verify_companies`, that calls each board and reports which tokens work. **Only commit entries that return a valid response.** The README must explain how to add a company.

---

## 5. Normalised Job schema

Every source maps to this one schema (`jobboard/models.py`):

| Field | Notes |
|---|---|
| `id` | Stable hash of the normalised company, title and location |
| `title` | |
| `company` | Display name |
| `company_normalised` | Lowercase, with "Ltd", "Limited", "PLC", "Inc", "Group", "UK" and punctuation removed |
| `company_website` | See §5.1 |
| `company_logo` | URL if available |
| `location` | Raw text |
| `city` | Normalised, e.g. "London", "Manchester", "Remote (UK)" |
| `work_mode` | onsite / hybrid / remote / unknown |
| `posted_at` | ISO date. If unknown, use first-seen and flag `posted_at_estimated` |
| `first_seen_at`, `last_seen_at` | Set by the database |
| `salary_min`, `salary_max`, `salary_text` | GBP where possible |
| `employment_type` | full-time / internship / contract / graduate scheme / … |
| `seniority` | From the source if given, otherwise inferred |
| `years_required` | Integer or null (§6.1) |
| `category` | §7 |
| `description_html`, `description_text` | Cleaned. Keep safe HTML (paragraphs, lists, bold) for display |
| `description_is_full` | bool |
| `requirements` | list[str], pulled out of the description (§6.3) |
| `apply_links` | list of `{source, url}`. Every place this job can be applied to |
| `sources` | list of source names |
| `is_active` | False if not seen in the last N refreshes or past its expiry date |

### 5.1 Company website

In order of priority:

1. The `website` from `companies.yaml`
2. The company website from Apify's company details
3. A website already known for the same `company_normalised` from any other job in the database (keep a `companies` table that caches this)
4. Fallback: a link to a Google search for `"<company> official website"`, labelled "Find website" so it's clear it's a guess

Never make up a domain.

---

## 6. Processing pipeline

`refresh.py` runs: **fetch all configured sources in parallel → normalise → extract (§6.3) → filter (§6.1, §6.2) → dedupe (§6.4) → categorise (§7) → upsert into SQLite → mark jobs that have disappeared as inactive.**

### 6.1 Experience filter: 0–4 years

Keep a job if **all** of these are true:

- The title does **not** match (case-insensitive, whole word): `senior, sr, lead, principal, staff, head of, director, vp, vice president, chief, manager of managers, partner, architect` (make architect configurable, since "solutions architect" roles can be junior). Keep "graduate", "junior", "associate", "intern", "trainee", "apprentice", "entry".
- The source's seniority, if given, is not Mid-Senior, Director or Executive.
- The **minimum years of experience** pulled from the description is **4 or less**, or none is stated. Parse patterns such as `3+ years`, `2-4 years`, `at least 5 years`, `minimum of 6 years`, `five years`, `5 yrs`. For a range, use the lower number. Store the result in `years_required`.

Log how many jobs each rule removes, so the rules can be tuned. Keep all thresholds in `settings.yaml`.

### 6.2 Location filter

Keep jobs in **England**, and remote-UK jobs. Put a list of English cities and regions (London, Manchester, Birmingham, Leeds, Bristol, Cambridge, Oxford, Reading, Brighton, Newcastle, Nottingham, Sheffield, Liverpool, Milton Keynes, …) in `settings.yaml`. Drop jobs that are clearly in Scotland, Wales, Northern Ireland or outside the UK.

### 6.3 Extraction (`extract.py`)

- **Requirements:** find headings in the description, whether HTML headings, bold lines or lines ending in ":", matching patterns like *requirements, what you'll need, what we're looking for, about you, you have, qualifications, skills, must have, who you are, essential, desirable*. Collect the bullet points or lines under them until the next heading. If there are no headings, fall back to bullet points containing words like "experience", "degree", "knowledge of", "proficient". Return at most 12 short items.
- **Salary:** parse £ amounts and ranges from the text when the source doesn't give them (including "£30k–£35k", "per annum", "pro rata").
- **Work mode:** detect hybrid, remote or on-site.
- Unit-test each of these with realistic sample descriptions.

### 6.4 Duplicate removal (`dedupe.py`)

The same job often appears on LinkedIn, Reed, Adzuna and the company's own site.

- **Group jobs by** `company_normalised` (exact match, or rapidfuzz ratio ≥ 92) **and** title similarity (normalise first by lowercasing and removing brackets, "- London", "(Hybrid)" and reference codes; rapidfuzz `token_set_ratio` ≥ 90) **and** a compatible city.
- **Merge** each group into one job:
  - Prefer the record with `description_is_full=True` and the longest description.
  - Priority for which source's details to keep: company career page > LinkedIn > Reed > Adzuna.
  - `posted_at` is the earliest date in the group.
  - Combine `apply_links` and `sources` from every record (no repeats).
  - Fill any empty fields (salary, website, logo) from the other records.
- Also remove duplicates **across refreshes**: a job already in the database keeps its `id` and `first_seen_at`, and `last_seen_at` is updated.
- Test with tricky cases: same job with a slightly different title, same company written differently ("Monzo Bank Ltd" vs "Monzo"), and genuinely different roles at the same company that should **not** be merged (e.g. "Graduate Software Engineer – Backend" vs "Graduate Software Engineer – iOS").

---

## 7. Categories (`categorise.py` + `config/categories.yaml`)

Assign each job to exactly one category, using ordered keyword rules on the **title**. If nothing matches, fall back to the description and then to "Other". The first match wins, so put more specific categories first.

Starting categories (edit freely in YAML):

1. **Software Engineering** (software engineer, developer, backend, frontend, full stack, mobile, iOS, Android, devops, SRE, platform, QA/test engineer)
2. **Data & Analytics** (data analyst, BI, analytics, insight, data engineer)
3. **AI / Machine Learning** (machine learning, ML, AI, data scientist, research engineer, NLP, computer vision). Check this before Data so "Data Scientist" goes here.
4. **Cyber Security**
5. **Product Management**
6. **Design / UX**
7. **Finance & Accounting** (analyst in finance context, accountant, audit, tax, FP&A, actuarial)
8. **Banking & Investment** (investment banking, trading, markets, asset management, equity research, quant)
9. **Consulting & Strategy**
10. **Marketing & Content**
11. **Sales & Business Development**
12. **Operations & Supply Chain**
13. **HR & Recruitment**
14. **Legal & Compliance**
15. **Engineering (non-software)** (mechanical, electrical, civil, chemical, hardware)
16. **Healthcare & Science**
17. **Customer Success & Support**
18. **Other**

Write tests for at least 40 example titles.

---

## 8. Refresh button, cooldown and Apify budget

- `POST /api/refresh` starts the refresh in a **background thread** and returns immediately. If a refresh is already running, it returns 409.
- `GET /api/refresh/status` returns overall progress plus each source's status (pending / running / done / skipped / error), how many jobs it found, and any error message. The page checks this every second or so while a refresh is running.
- **Free sources** (Greenhouse, Lever, Ashby, Reed, Adzuna) run on every refresh.
- **Apify / LinkedIn** only runs if:
  - at least `apify_cooldown_minutes` (default **60**) have passed since the last Apify run, **and**
  - the estimated spending this month plus the estimated cost of this run stays under `apify_monthly_budget_usd` (default **4.00**, leaving room under the $5 free credit).
  - Estimate the cost as `results × price_per_1000 / 1000`, with the price set in `settings.yaml` (default 0.10), and log every run in the database. If the Apify API offers a way to read actual monthly usage (e.g. `GET /v2/users/me/limits`), use that as the true figure.
  - `max_results_per_apify_run` defaults to **1000**.
- The page shows when LinkedIn was last refreshed, when it can next refresh, and the Apify spending so far this month (e.g. "$1.20 of $4.00 used"). If Apify is skipped, show why.
- Add a "Force LinkedIn refresh" option that ignores the cooldown but **never** the budget cap.
- A command-line refresh must also work: `python -m jobboard.refresh`. That way I can schedule it later with cron or launchd if I want.

---

## 9. `config/settings.yaml` (starting values)

```yaml
locations:
  primary: "London, England, United Kingdom"
  secondary: ["England, United Kingdom"]
  england_cities: [London, Manchester, Birmingham, Leeds, Bristol, Cambridge, Oxford,
                   Reading, Brighton, Newcastle, Nottingham, Sheffield, Liverpool,
                   Milton Keynes, Southampton, Leicester, Coventry, York, Bath, Guildford]
search_keywords:        # broad, to cover every category
  - graduate
  - junior
  - entry level
  - associate
  - analyst
  - software engineer
  - data
  - marketing
  - finance
  - consultant
  - product
  - designer
  - operations
  - sales
  - intern
experience:
  max_years: 4
  excluded_title_words: [senior, sr, lead, principal, staff, "head of", director, vp,
                         "vice president", chief, partner]
apify:
  actor: "supreme_coder~linkedin-jobs-scraper"
  cooldown_minutes: 60
  monthly_budget_usd: 4.00
  price_per_1000_usd: 0.10
  max_results_per_run: 1000
  posted_within_first_run: r604800
  posted_within_routine: r86400
reed:
  results_per_query: 300
  detail_concurrency: 5
adzuna:
  max_pages_per_query: 3
  max_days_old: 14
dedupe:
  company_threshold: 92
  title_threshold: 90
inactive_after_missed_refreshes: 3
```

---

## 10. The web page (`static/index.html`), modelled on newgrad-jobs

Clean, fast and readable. Light and dark mode (follow the system setting). Works on mobile.

**Header**
- Title: "London Early-Careers Jobs"
- Stats: **new today**, **total active jobs**, **last updated** (relative time)
- The **Refresh** button, with a progress panel that shows each source's status while it runs
- Apify budget / cooldown info (small text)

**Category tabs**
- A row of tabs, "All" plus one per category, each with a job count. The selected tab is kept in the URL (e.g. `?c=software-engineering`) so it can be linked or bookmarked, like newgrad-jobs' `?k=` links.

**Filters bar**
- Search (title, company, description)
- Location (city dropdown)
- Date posted (24h / 3 days / 7 days / 30 days / any)
- Work mode
- Source
- Salary listed (toggle)
- "Hide jobs I've marked as applied" (store in localStorage, wrapped in try/catch)

**Job table**, sorted by date posted (newest first) by default, with sortable columns:
- Company logo and name, with the name linking to the **company website** (open in a new tab)
- Job title, plus a **NEW** badge if first seen since the last refresh
- Category tag
- Location and work mode
- Salary (if known)
- Date posted (e.g. "2d ago", with the exact date on hover)
- Source icons (LinkedIn / Reed / Adzuna / Company site)
- **Apply** button, which opens the best apply link (company site first). If there are several apply links, show a small menu of all of them.

**Expanded row** (click a row):
- **Requirements** section (bullet list pulled from the description)
- **Full description** (sanitised HTML, scrollable). For snippet-only jobs, show the snippet and a "View full listing →" link.
- Metadata: employment type, years required, first seen, sources

**Other**
- Pagination or virtual scrolling that stays fast with 10,000+ jobs
- A "Mark as applied" checkbox on each job (localStorage)
- A helpful empty state and error states
- The page fetches jobs from `GET /api/jobs` (returns all active jobs; filtering happens in the browser)

**Server routes:** `GET /` (the page), `GET /api/jobs`, `GET /api/stats`, `POST /api/refresh`, `GET /api/refresh/status`.

**Security:** sanitise all description HTML before rendering it (use `bleach` or similar on the server, with an allowlist of tags). The server listens on `127.0.0.1` only.

---

## 11. Git and GitHub

- Repo name: **`london-early-careers-jobs`**. Description: "Early-career (0–4 yrs) job board for London & England — aggregates LinkedIn (via Apify), Reed, Adzuna and company career pages, deduplicated and grouped by category."
- **Ask me whether the repo should be public or private** before creating it. Create it with `gh repo create` (check that `gh auth status` works first; if it doesn't, tell me how to log in).
- Add topics: `job-board`, `python`, `flask`, `london`, `uk-jobs`, `graduate-jobs`.
- `.gitignore` must include: `.env`, `data/`, `*.db`, `__pycache__/`, `.venv/`, `.pytest_cache/`, `.DS_Store`.
- **Never commit API keys or the database.** Check before each commit (`git diff --cached`).
- Commit in logical steps, using Conventional Commit style messages, for example:
  1. `chore: project scaffold, requirements, gitignore, licence`
  2. `feat(core): job model and SQLite storage`
  3. `feat(sources): Greenhouse, Lever and Ashby boards + company list`
  4. `feat(sources): Reed with full-description fetching`
  5. `feat(sources): Adzuna`
  6. `feat(sources): LinkedIn via Apify with cooldown and budget cap`
  7. `feat(pipeline): extraction, experience filter, location filter`
  8. `feat(pipeline): fuzzy deduplication`
  9. `feat(pipeline): categorisation`
  10. `feat(ui): job board page with tabs, filters, expandable rows, refresh`
  11. `test: unit tests for extract, filters, dedupe, categorise`
  12. `docs: README with setup, keys, screenshots`
- Push to `main` when it's working. Use feature branches for later changes.

**README.md** should cover: what the app does, a screenshot, quick start (`python -m venv .venv`, `pip install -r requirements.txt`, `cp .env.example .env`, `python app.py`), how to get each API key (with links), which sources work without keys, the Apify cost and budget explanation, how to add companies and categories, and how to schedule automatic refreshes with `launchd`.

---

## 12. `.env.example`

```
APIFY_TOKEN=
REED_API_KEY=
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
```

---

## 13. Done when

- [ ] `python app.py` opens the board in the browser, and it works with **no API keys**, using the company-career-page sources only.
- [ ] With keys added, a refresh pulls from all five kinds of source, and the progress panel shows each one.
- [ ] Reed jobs show **full** descriptions, and later refreshes only fetch details for new jobs.
- [ ] Apify is not called again within the cooldown, and never beyond the budget cap. The page shows the reason when it's skipped.
- [ ] The same job from several sources appears **once**, with all its apply links.
- [ ] No jobs with "Senior"/"Lead"/etc. titles, and none requiring 5+ years.
- [ ] Every job has a category, a posted date, a company website link (or the clearly labelled "Find website" fallback), and an apply link.
- [ ] Expanded rows show the Requirements and the full description.
- [ ] The page stays fast with 10,000+ jobs.
- [ ] `pytest` passes.
- [ ] The repo is on GitHub with a clean commit history and a complete README, and there are no secrets or data files in the history.
- [ ] At the end, give me a summary: jobs per source, duplicates removed, jobs dropped by each filter, and any problems with a source.
