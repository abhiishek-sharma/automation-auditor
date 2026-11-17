# Automation Doctor — MVP Implementation Plan

**Deadline:** working local MVP by Sunday 22 Nov 2026 · **Branch:** `claude/mvp` · **Language:** Python ≥ 3.10, standard library only

## Problem
A cron job can exit 0 (or get HTTP 200) and still fail to do its job. Automation Doctor runs the job (or inspects a
finished run), checks the *outcome*, and reports any mismatch between what the job reported and what it actually did,
with evidence and a suggested fix. It never changes the job.

## Scope (MVP)
In: local cron/script jobs, a JSON job spec (YAML if PyYAML happens to be installed), three outcome-check types,
deterministic classification, an optional local LLM diagnosis, terminal and HTML reports, and four demo cases.
Out: agent platform, n8n/Airflow integrations, auto-repair, remote alerting, scheduling (cron still does the scheduling).

## Architecture
```
job.json ──► config ──► runner ──► RunRecord (exit code, stdout/stderr tails, timestamps)
                                         │
                                         ▼
                            checks (file | sqlite | http) ──► CheckResult[] (pass/fail/error + evidence)
                                         │
                                         ▼
                 classify ──► OK | SILENT_FAILURE | REPORTED_FAILURE | UNVERIFIED
                                         │ (only when not OK)
                                         ▼
          evidence bundle (trimmed, redacted, control chars stripped)
                                         │
                                         ▼
          DiagnosisProvider: ollama ─(unreachable/invalid)─► rules (deterministic fallback)
                                         │
                                         ▼
                 report: terminal text + self-contained HTML + run JSON in .adoc/runs/
```

| Module | Responsibility |
|---|---|
| `config.py` | Load/validate the job spec; `${VAR}` substitution (`RUN_DATE`, `JOB_DIR`, env). |
| `runner.py` | Run the command with a timeout, or build a record from a finished run (`inspect`). |
| `checks.py` | `file` (exists, fresh since run start, JSON field / text), `sqlite` (read-only query, row count / value), `http` (GET, status, JSON field / text). Each returns structured evidence. |
| `classify.py` | Pure function: exit status + check results → verdict. |
| `evidence.py` | Builds the minimal bundle the model sees; redacts secrets, truncates, strips control chars. |
| `providers/` | `DiagnosisProvider` interface; `OllamaProvider` (HTTP to localhost, JSON-schema output, temp 0); `RulesProvider` (offline heuristics). Model output is validated against a fixed schema and length-capped. |
| `report.py` | Terminal and HTML (all values HTML-escaped; suggested fixes labelled "not applied"). |
| `demo.py` | Runs every demo job, writes reports and a measured `summary.json`. |

## Verdicts and CLI exit codes
`OK`=0 · `SILENT_FAILURE` (exit 0, a check failed)=1 · `REPORTED_FAILURE` (non-zero exit/timeout)=2 ·
`UNVERIFIED` (exit 0 but a check could not be evaluated)=3. Cron can wrap jobs with `automation-doctor run job.json`.

## Untrusted input
Logs, files, HTTP bodies and model output are data, never instructions. Evidence is wrapped in delimiters and the
prompt says so; the model is never given tools; its JSON is schema-validated, enum-checked and truncated; the report
escapes everything; nothing it suggests is executed. SQLite checks open the DB read-only.

## Demo cases
1. `wrong_file` — exits 0 but writes `report.json` instead of `report-<date>.json` (stale file present too).
2. `http_missing_data` — API returns 200 with an empty `items` list; job exits 0.
3. `duplicate_on_retry` — insert commits, simulated timeout, retry inserts again; exits 0.
4. `healthy` — correct output; no alert.

## Tests (unittest)
Each check type pass/fail/error paths, freshness, classification table, evidence redaction/truncation, model-output
validation (malformed JSON, wrong types, prompt-injection text), Ollama-unavailable fallback, end-to-end demo verdicts.

## Milestones
1. Plan + skeleton · 2. checks + classify + runner + tests · 3. rules provider + reports + demo → **handoff #1** ·
4. Ollama provider + measure Qwen 3.5 9B on M1 16 GB · 5. README, limitations, measured results → **final handoff**.
