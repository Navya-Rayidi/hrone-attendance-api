# Review notes

Use this file to record verified defects from the supplied starter and the exact fixes you make. Do not claim a test passed unless you ran it.

## Suggested cases to verify

- [ ] Invalid epoch timestamps (seconds, floats, strings, out-of-range values) return 422.
- [ ] Duplicate employee codes and duplicate punch-ins return 409 under concurrent requests.
- [ ] Overnight shifts assign attendance to the correct IST date.
- [ ] Late grace boundary: 09:40:00 is not late for a 09:30 shift; 09:40:01 is late.
- [ ] Work hours use half-up rounding and half-day is based on rounded hours.
- [ ] Regularization preserves prior history and rejects no-op changes.
- [ ] Analytics are aggregation-backed and remain efficient on large datasets.
- [ ] Explain output uses index scans for the main query.

## Findings

Add each finding with: location, failing input/symptom, cause, and fix.
