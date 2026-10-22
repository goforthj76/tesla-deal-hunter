# Antigravity handoff: restore the user's exact preferred UI

The user prefers their original Antigravity dashboard, shown in their October 22 screenshot, over the merged preview. Restore that design, retaining the updated backend features. The current merge is NOT a pixel-exact reproduction.

## Visual target
- Centered approximately 1200px desktop dashboard on near-black background.
- Compact header: red lightning square, title and search summary left; monitoring status and red Scan All Now control right.
- Slim Telegram status banner above four equally sized metric tiles.
- Four-column desktop vehicle grid, with responsive fewer-column mobile layouts.
- Tabs: Best Deals, Sources & Scanners, Live Activity, Settings; integrate web discovery without replacing the original layout.
- Small pill filters, single-row search plus sort, result count.
- Cards: fixed-height image, top-right outlined numeric score badge, green price with source aligned right, model/year then trim, mileage and distance, compact evidence/dealer tags, brief scoring explanation panel, blue listing button and small star button.
- Use the original Antigravity frontend at commit 2c4952f01043fc2e93c5a937a78d1860f704358b as a starting point. Do not revert the entire backend to that commit.
- Screenshot counts and cars are reference content, not fixtures to hardcode. The screenshot shows a different running database than the local merge.

## Preserve from merged commit 027be38
- Tavily server-side discovery, URL deduplication, four-hour schedule and local monthly quota guard.
- Separate unverified search leads from actual VIN inventory.
- VIN checksum validation, source provenance, price history, watchlist, scoring breakdown and hunt reports.
- Existing database and .env. Back up the database before migration; preserve extra legacy columns.
- User-confirmed preferences: Model 3/Y, 50-mile Flower Mound radius, max $36,000, preferred mileage under 50,000; prioritize newest and low mileage, with Long Range/AWD benefits. Purchased FSD is a bonus only with evidence.
- Never infer confirmed HW4 or permanent FSD solely from a model year, VIN, or a seller phrase. Keep unknown distance and unverified evidence explicit.
- Historical MarketCheck records remain historical unless refreshing/retention is authorized. Current merge disables scheduled MarketCheck archival.
- Source status and scan counters must reflect actual successful work, not merely an adapter existing.

## Known limits and integration work
- Current merge has two scheduled direct-dealer collectors; older Antigravity adapters are retained but not enabled by the merged scheduler. Review and validate each before restoring it.
- Automatic Telegram deal alerts are not wired into the merged scheduler; only an explicit test endpoint exists. Preserve configured secrets and validate eligibility/deduplication before restoring alerts.
- Public inventory auto-publication and automatic ngrok startup are disabled in the merge. GitHub source publication is not deployment. Establish intentional authenticated phone access separately.
- Tavily can return irrelevant or out-of-area pages; improve relevance filtering without silently counting these as local vehicles.
- The user-provided screenshot is available in their chat. Ask them to attach it to the Antigravity task if unavailable there; no screenshot containing browser context has been uploaded to this public repo.

## Validation
The merged build passed 30 automated tests and a basic desktop browser check. This does not establish live validity of every collector, correct phone deployment, or zero defects.
Run tests again after integrating the original frontend. Verify sorting, filters, vehicle detail, saved cars, settings, source statuses and discovery; verify responsive layout and browser errors. Use safe DOM rendering and keep API keys out of browser code, logs and Git.
