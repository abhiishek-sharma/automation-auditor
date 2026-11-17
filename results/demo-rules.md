# Automation Auditor: demo results

- Generated: 2026-11-17T18:54:16+05:30 by `automation-auditor demo --provider rules`
- Diagnosis model: -
- Scope: this repo's own synthetic scenarios (`demo/jobs/`). This is not a real-world detection rate.

**Verdicts:** 10/10 scenarios classified as labelled · alerts on healthy runs: 0 · missed silent failures: 0

**Diagnosis keyword check (crude relevance check):** 7/7 diagnoses mention an expected cause keyword · LLM fallbacks to rules: 0 · median latency: None ms · max: None ms

| Scenario | Job exit code | Expected | Actual | Match | Diagnosis by | Uncertainty | Cause keyword |
|---|---|---|---|---|---|---|---|
| 01_healthy_report | 0 | OK | OK | yes | - | - | n/a |
| 02_wrong_file | 0 | SILENT_FAILURE | SILENT_FAILURE | yes | rules (heuristics-v1) | low | yes |
| 03_http_missing_data | 0 | SILENT_FAILURE | SILENT_FAILURE | yes | rules (heuristics-v1) | medium | yes |
| 04_duplicate_on_retry | 0 | SILENT_FAILURE | SILENT_FAILURE | yes | rules (heuristics-v1) | low | yes |
| 05_stale_output | 0 | SILENT_FAILURE | SILENT_FAILURE | yes | rules (heuristics-v1) | medium | yes |
| 06_swallowed_error | 0 | SILENT_FAILURE | SILENT_FAILURE | yes | rules (heuristics-v1) | medium | yes |
| 07_partial_batch | 0 | SILENT_FAILURE | SILENT_FAILURE | yes | rules (heuristics-v1) | medium | yes |
| 08_healthy_retry | 0 | OK | OK | yes | - | - | n/a |
| 09_healthy_api_sync | 0 | OK | OK | yes | - | - | n/a |
| 10_reported_failure | 1 | REPORTED_FAILURE | REPORTED_FAILURE | yes | rules (heuristics-v1) | medium | yes |
