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
`UNVERIFIED` (exit 0 but a check could not be evaluated)=3. Cron can wrap jobs with `automation-auditor run job.json`.

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

---

## Handoff 2 (Thu 2026-11-17) — B1–B8 complete

**Branch:** `claude/mvp` · **Remote:** https://github.com/abhiishek-sharma/automation-auditor · **CI:** green (ubuntu-latest + macos-latest, Python 3.12)

### What runs
- `make setup && make test` → 43 tests pass, no model or network needed
- `make demo` → 10 scenarios, 10/10 verdicts correct, 0 false alerts, writes `demo-output/index.html`
- `make demo-llm` → same with qwen2.5-coder:7b via local Ollama; verdicts unchanged (deterministic)
- `automation-auditor run <spec>` / `inspect` / `validate` / `demo` / `status`

### Acceptance checklist B1–B8

| Item | Status | Notes |
|---|---|---|
| B1 Remote + CI | ✅ | `origin` pushed; Actions green on both OS |
| B2 `make setup && make demo` one command | ✅ | Makefile; no paid API key |
| B3 Deterministic verdict, LLM adds explanation only | ✅ | `classify()` runs before `diagnose()`; test sends hostile reply, asserts verdict unchanged |
| B4 Four check types: file / sqlite / http / output | ✅ | Each has pass/fail/error paths in tests |
| B5 10 demo scenarios (3 healthy, 7 failures) | ✅ | 10/10 labelled correctly by rules provider |
| B6 Untrusted-input pipeline | ✅ | Evidence trimmed (~6 KB), secrets redacted, HTML-escaped; SQLite read-only; a test shows DELETE fails even if spec validator is bypassed |
| B7 Measured results committed | ✅ | `results/demo-rules.md` (10/10) + `results/demo-ollama-qwen2.5-coder-7b.md` (10/10 verdicts, 7/7 keywords, ~48 s median) |
| B8 README | ✅ | Architecture diagram, check-types table, demo scenario table, example terminal output, measured results, add-your-own-job walkthrough, security model, limitations |

### Manager corrections applied
- Default model: `qwen2.5-coder:7b` (already pulled, 4.7 GB); Qwen 3.5 9B removed
- LLM framing: "matched the rules provider on cause keyword identification" — no accuracy improvement claimed
- Latency: re-measured after prompt tuning (num_ctx 4096, compact JSON, 6 KB evidence cap) → ~48 s median, max 68 s (was ~78 s)
- YAML: documented as optional (`pip install 'automation-auditor[yaml]'`); JSON works with zero deps

### Key commits on `claude/mvp`
| SHA | Message |
|---|---|
| `e6b18d4` | Automation Doctor MVP: outcome checks, verdicts, rule/Ollama diagnosis, reports, 10 demo scenarios |
| `868f19e` | Add measured demo results (rules, qwen2.5-coder:7b) |
| `8c5f6db` | Handoff 2: README, CI, examples, latency tuning, measured results |
| `2b364bc` | Rename automation-doctor → automation-auditor |
| `02c128e` | Fix stale automation-doctor references in results and docs |

### Known limitations (documented in README)
- Synthetic scenarios only; 10/10 on the same set they were written for
- LLM explanations slow (~48 s median) and not validated for correctness
- Redaction is pattern-based; review before sharing reports
- No Postgres/S3/queue checks, no auth flows beyond static headers
- Not included by design: scheduling, alerting, auto-repair, web UI

### Next step
Review `claude/mvp`, merge to `main`, or open a PR for review.
