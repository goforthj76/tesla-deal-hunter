# Backend handoff to Antigravity
Branch codex/vin-investigation, based on f74721b. UI, CSS and run.py unchanged.

GET /api/state: each vehicle now has investigation containing conflicts (field, values, message), opportunities, warnings, investigation_priority, purchase_readiness, hw4, fsd, history, freshness, checklist and provenance claims. HOLD changes category to MANUAL REVIEW. UI should show this category and prioritize readiness separately from numeric score; backend numeric score is not a purchase recommendation.
GET /api/investigate/{vin}: same investigation plus observations and VIN. Missing VIN inventory returns empty evidence, not invented facts.
POST /api/investigate/{vin}/evidence: JSON {field,value,kind,source,url}. Fields: trim, drivetrain, year, mileage, price, location, hw4, fsd, history, availability. Kinds: seller, aggregator, user_report, physical_inspection, history_report. All inputs are recorded user evidence, not authenticated documents. POST /api/investigate/{vin}/evidence/{id}/retract preserves the audit row.

Normal startup adds evidence and observations tables. Back up database first. Older data gains snapshot history when it changes; earlier unavailable history is not reconstructed.
Render all source values using textContent or equivalent escaping. Do not label user reports independently confirmed. Generic Base is a verification task; Base versus Long Range is possible misidentification, not proof of a bargain. Location string conflicts may reflect formatting or older moves: display dates and request physical-store confirmation.

Remaining: automatic exact-VIN search, address normalization/geocoding, source authority ranking, finer history-negation handling, comparable valuations. Existing dealer-name/keyword scoring heuristics still need refinement. No authoritative clean-title guarantee. Expanded discovery update remains separate. Tests include reported Long Range and Orlando examples, hardware versus purchased FSD, history conflict, generic trim, evidence API retraction.
