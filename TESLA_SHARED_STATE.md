# Tesla Deal Hunter — Shared Collaboration State

This file is the shared contract and handoff board between **Antigravity**, **Codex**, and **ChatGPT**.

---

## 1. Division of Ownership

| Agent / Contributor | Domain Ownership | Responsibilities |
| :--- | :--- | :--- |
| **Antigravity** | **Frontend, App Server & Live Deployment** | • UI design (`app/static/style.css`, `index.html`, `app.js`).<br>• Fast responsive layout, filter pills, evidence modal.<br>• FastAPI server lifecycle, ngrok tunnel, and Windows CPU keep-alive (`run.py`).<br>• Live site availability at `dehydrate-shading-venus.ngrok-free.dev`. |
| **Codex** | **VIN Evidence & Contradiction Engine** | • Deep VIN verification, checksums, and history checks.<br>• Contradiction & mismatch engine:<br>&nbsp;&nbsp;– Trim mismatches (e.g., advertised "Base" vs vehicle screen "Long Range").<br>&nbsp;&nbsp;– Title & salvage red flags (branded, rebuilt, auction totals).<br>&nbsp;&nbsp;– Location conflicts (advertised local Dallas vs actual out-of-state lot).<br>&nbsp;&nbsp;– Software validation (HW4 confirmation, distinguishing paid FSD vs temporary trial).<br>• Backend unit tests in `tests/`. Work on feature branches (e.g., `codex/vin-verification`). |
| **ChatGPT / Search** | **Lead Discovery & Analysis** | • Independent listing review via `/eval` and `https://raw.githubusercontent.com/goforthj76/tesla-feed/main/tesla-feed.md`.<br>• Manual seeds, user preference adjustments, and physical buyer inspection checklists. |

---

## 2. Current Project State

* **Repository**: `https://github.com/goforthj76/tesla-deal-hunter` (branch `main`)
* **Live Web App**: [https://dehydrate-shading-venus.ngrok-free.dev](https://dehydrate-shading-venus.ngrok-free.dev)
* **Local Web App**: `http://127.0.0.1:8765`
* **ChatGPT Feed**: [https://dehydrate-shading-venus.ngrok-free.dev/eval](https://dehydrate-shading-venus.ngrok-free.dev/eval)
* **Active Database**: SQLite `data/hunter.db` (913 tracked vehicles, 46 deals with score 70+)
* **User Preferences**:
  * Location: Zip 75022 (Flower Mound, TX)
  * Radius: 50–100 miles
  * Max Price: $33,000 (Target ideal: $28,000–$31,000)
  * Models: Model 3 / Model Y
  * Hardware: HW4 (2024+ confirmed, or late-2023 verified Austin/Fremont build)
  * Mileage: Preferred under 50,000 miles

---

## 3. Collaboration Rules & Protocols

1. **Never Overwrite Each Other's Domain**:
   * Codex touches verification logic (`app/analysis.py`, `app/scoring.py`, new verification modules) and `tests/`. Codex should NOT revert `app/static/` or replace the frontend design.
   * Antigravity connects Codex's flags and fields into the UI (`app/static/app.js` and `detail-modal`) and preserves backend test suites.
2. **Branching Workflow**:
   * Codex: create and work in `codex/*` branches (e.g. `codex/vin-investigation`).
   * PR / Merge cleanly into `main` after running `pytest tests/`.
3. **Database & Secret Protection**:
   * `data/hunter.db` and `.env` are persistent and Git-ignored.
   * Never commit API keys or plain passwords to Git.

---

## 4. Active Shared Task Board

- [x] **[Antigravity]** Restore exact preferred 1200px dark Antigravity UI with responsive 4-column card grid.
- [x] **[Antigravity]** Add interactive "🔍 Details" modal for deep evidence inspection and observed price history.
- [x] **[Antigravity]** Expose live endpoints (`/api/state`, `/api/discovery`, `/api/research-seeds`, `/eval`, `/api/marketcheck-key`).
- [x] **[Antigravity]** Establish Windows CPU keep-alive and permanent ngrok tunnel in `run.py`.
- [ ] **[Codex]** Implement VIN discrepancy & mismatch engine:
  - Detect trim discrepancies (advertised vs build specs).
  - Cross-check dealer address vs physical lat/long (catch out-of-state cars listed with virtual Dallas addresses).
  - Explicitly tag adverse title history (salvage, rebuilt, total loss, AutoSavvy inventory).
  - Distinguish confirmed HW4 from inferred HW4, and paid FSD from trial FSD.
- [ ] **[Antigravity]** Ingest Codex's discrepancy tags into the dashboard:
  - Add visual flags (e.g. `⚠️ Trim Discrepancy`, `⚠️ Out of Area`, `🚨 Title Alert`) on vehicle cards and within the details modal.
- [ ] **[Codex & Antigravity]** Automatic Telegram deal alerts:
  - Wire verified high-scoring deals (Score >= 75 with no discrepancy flags) to trigger phone buzzers to Chat ID `8708168453`.
