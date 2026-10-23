# Automatic inventory-wide VIN research

Based on deployed 2b7b028. Preserve .env, database, existing runner and dashboard style. Merge this branch, back up hunter.db using SQLite backup, and restart the existing run.py once (one scanner).

Every checksum-valid Vehicle VIN is automatically queued, regardless of budget, age or listing status. Queue state lives in the existing settings JSON, so no new schema migration is required. Three of each four discovery calls research VINs; the remaining call discovers inventory. All searches share the existing 750-call local monthly cap, four-hour cycle and reserved-before-request accounting. Pending VINs go before refreshes; completed VINs refresh after 30 days; failed or interrupted attempts retry after four hours without being counted as completed.

Search results require an exact VIN. Single-VIN snippets can supply explicitly unverified trim claims. These flow automatically into both dashboard investigation results and VIN detail comparisons. Multi-VIN snippets are not attributed to a specific car. This is not full-page verification, an authoritative trim decoder, purchased-FSD verification or a title report. Snippets can still be wrong. No result never means clean history.

/api/vin-queue and /investigator show all VINs, per-VIN status, completion, errors, remaining budget and a lower-bound first-pass estimate. At 3 VINs per 4 hours, 914 VINs need at least 51 days; 750 shared monthly calls permit at most roughly 562 VIN queries per month if all discovery cycles use three VIN queries. Actual timing depends on remaining credits, restarts and failures. Do not advertise every VIN as externally checked until its job succeeds.

Validation: 57 tests, plus JS syntax check. Regression cases cover all-VIN enrollment, older/over-budget car research, fair queue selection, retry accounting, no exact matches, mixed-VIN rejection and automatic dashboard mismatch propagation.

After restart: GET /api/vin-queue (total should match valid tracked VINs), verify first worker cycle saves vin_jobs, and confirm no second scanner is running. Existing discovery cooldown remains respected; do not reset budgets to force a demo.

## Serper integration update
Private live .env now contains SERPER_API_KEY, SERPER_MAX_CALLS=2000 and SERPER_PREUSED_CALLS=2. Never commit these values or the .env. Two live test requests returned HTTP 200 in 1.47s and 0.94s. No inventory-wide live run has been started.

Serper uses the shared queue and discovery lock, up to 10 serialized searches per 60-second cycle. Lifetime reservation persists in discovery JSON and does not reset monthly. Preused test calls count toward the cap. Provider errors halt the batch with a four-hour backoff. The 2000-call cap leaves headroom against the advertised 2500 trial, but is not an account-balance check; usage outside this app is not tracked. Do not enable paid refills.

The earlier 51-day estimate applies only to Tavily-only mode. Serper mode targets up to 600 searches/hour, actual throughput lower depending on network and other collectors. UI reports completed jobs and measured last-batch duration rather than claiming universal verification.

Validation now: 59 tests pass plus JS syntax check. Merge the updated PR #2 and restart the existing runner once. Verify /api/vin-queue reports serper.configured=true and reserved at least 2; verify completion count increases after a batch. Preserve existing DB and .env.
