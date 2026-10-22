# Tesla Deal Hunter

Private local Tesla inventory research dashboard for Flower Mound, Texas.

## Run on Windows

Install Python 3.12+, then in this directory:

```powershell
py -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe run.py
```

On the original computer dependencies are already installed: double-click Start.cmd.
The browser opens at http://127.0.0.1:8765. Keep the computer awake and scanner running.

## Current capabilities

- Persistent SQLite inventory, VIN deduplication, watchlist and price changes.
- Model 3/Y preference screening, 50-mile distance checks and explicit unknowns.
- Autohub Texas and Autos of Dallas collectors with conservative schedules.
- Source status, local API budget tracking, search/sort and overnight report sessions.
- Reports survive restarts; they measure activity without overriding source cooldowns.
- Historical MarketCheck records are retained locally but are not refreshed by default.

MarketCheck scheduled archival is disabled pending suitable retention permission.
Its presence in the code is not permission to persist provider data. Only set
MARKETCHECK_RETENTION_AUTHORIZED=true after obtaining appropriate authorization.
No API key is required for the two direct dealer collectors. Setup.cmd supports
masked local key replacement; do not commit keys or send them through GitHub issues.

## How it works

Discovery adapters normalize listings into source-specific records. Vehicles are
merged by VIN while each source retains its provenance. Screening separates
rejected cars from candidates and manual review. Purchased FSD, hardware, title,
accident history and battery condition remain unverified until evidence is obtained.
A WATCH label is not a verified deal. Preference score is not market valuation.
Start an overnight report to capture changes during a run, then finish it in the morning.
Finishing a report does not stop the normal scanner.

## Privacy and deployment

This repository excludes local databases, logs, API keys, virtual environments,
private screenshots and hosting identity. Your local database is not uploaded.
Bind to localhost by default. LAN access requires TDH_HOST and a TDH_PASSWORD of
at least 16 characters; use only a trusted network. Do not expose this HTTP server
to the internet. GitHub stores source code; it does not provide an always-running
scanner or deploy this backend automatically.

The cloud prototype is included separately for development, not a working deployment.
Hosting, phone access with PC off, push alerts, Auto.dev integration, independent
history reports and full dealer coverage remain unfinished.

## Development

```powershell
.venv/Scripts/python.exe -m pytest -q
```

Use mocked responses during development to avoid consuming API quota.


## Ranking and VIN checks

Default ranking prioritizes active WATCH candidates, manual review, rejected cars,
historical-only snapshots, then unavailable records. Within each tier: score,
model year and mileage. Explicit price/mileage sorts remain available.
Score weights: model year 35, mileage 35, budget fit 10, Long Range 10,
AWD 5, Model Y 5. Details show each contribution. This is a preference score,
not an estimate of market value. Purchased-FSD claims do not earn unverified credit.
The VIN check validates format and check digit; NHTSA decoding and history searches
are not implemented. Unknown/older hardware and invalid VINs require manual review.

More nearby coverage requires additional authorized sources; increasing scan frequency
only repeats existing records. Auto.dev and generic dealer-platform adapters remain
unimplemented. Existing MarketCheck snapshots must not be mistaken for live coverage.


## Tavily discovery

Set TAVILY_API_KEY in your local .env and restart. Search the web now includes
Tavily status and discovered links. The scanner performs one basic search every
four hours across twelve nearby towns, up to 300 reserved calls per calendar
month (roughly 180-186 on the default schedule). Other apps using your key are
not counted. Failed attempts reserve budget conservatively. No paid fallback,
advanced search, extraction or raw-page fetching is enabled.

Canonical URLs are merged and up to 2,000 recent leads persist locally. The UI
shows the latest 100. These are discovery leads, NOT VIN-confirmed inventory;
search towns do not enforce distance. No automatic crawling of discovered sites
occurs. VINs, price, availability and the 50-mile boundary need verification.
The manual button respects the same cooldown and budget as scheduled searches.
Existing vehicle inventory and secrets are not changed by this integration.

## Unified Antigravity merge

Antigravity visual styling is combined with the evidence dashboard, Tavily, saved cars and overnight reports. Confirmed defaults are 50 miles, Model 3/Y, $36,000 maximum and under 50,000 miles preferred. Gemini report dealer URLs are research references, not verified inventory. Telegram manual test is available when configured; automated alerts remain disabled. No automatic inventory uploads or public tunnels run. Alternative marketplace adapters require source-access review before enabling.
