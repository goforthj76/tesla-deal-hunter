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
