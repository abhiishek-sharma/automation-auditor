# Automation Doctor

A scheduled job can exit 0, or get an HTTP 200, and still not do its job. It writes the wrong file, saves an empty
API response, or inserts a batch twice after a retry. Automation Doctor runs the job (or inspects a run that already
happened), **checks the outcome**, and reports any mismatch between what the job claimed and what actually happened.
Each report includes the evidence and a suggested fix.

- Local and offline. Python standard library only (PyYAML is optional). No API key, no paid service.
- The verdict comes from deterministic checks. A local LLM (Ollama) can add a plain-English explanation of the
  evidence. It cannot change the verdict or the exit code.
- It never modifies your jobs. Suggested fixes are text for a human to review.

## Quick start (macOS, Python 3.12 via uv)

```bash
git clone <repo-url> automation-doctor && cd automation-doctor
make setup        # uv venv --python 3.12 .venv && uv pip install -e '.[dev]'
make test         # 43 tests; no model needed
make demo         # runs the 10 demo scenarios, writes demo-output/index.html
open demo-output/index.html
```

Without `make` or `uv`: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/python -m
automation_doctor demo`.

Optional LLM explanation (needs [Ollama](https://ollama.com) installed locally):

```bash
brew install ollama && ollama serve &        # skip if Ollama is already running
ollama pull qwen2.5-coder:7b                 # about 4.7 GB on disk; the model used for the results below
make demo-llm                                # slow: expect ~48 s median per failing scenario on an M1 / 16 GB
```

## What it does

```
job spec (JSON/YAML) ─► run command ─► run record: exit code, stdout/stderr, start/end time
                                │
                                ▼
         outcome checks: file │ sqlite │ http │ output   ─► pass / fail / error + evidence
                                │
                                ▼
      verdict (deterministic):  OK │ SILENT_FAILURE │ REPORTED_FAILURE │ UNVERIFIED
                                │  only if not OK
                                ▼
   evidence bundle (trimmed, secrets redacted, control chars stripped)
                                │
                                ▼
   diagnosis provider:  rules (offline heuristics)  │  ollama (local model; falls back to rules)
                                │
                                ▼
          report: terminal │ JSON │ Markdown │ HTML      +  process exit code 0/1/2/3
```

| Verdict | Meaning | Exit code |
|---|---|---|
| `OK` | The job reported success and every outcome check passed. No alert. | 0 |
| `SILENT_FAILURE` | **The job reported success, but the outcome is wrong.** | 1 |
| `REPORTED_FAILURE` | The job itself failed (non-zero exit, timeout, could not start). | 2 |
| `UNVERIFIED` | The job reported success, but a check could not be evaluated (bad query, endpoint down). | 3 |

Exit code 4 means a usage or config error.

### Checks

| Type | Verifies | Key options |
|---|---|---|
| `file` | A file exists, was written **during this run** (`fresh`), and has a JSON field or text | `path`, `fresh`, `json_field` + `op`/`value`, `contains` |
| `sqlite` | A read-only query returns the expected row count or value (e.g. no duplicate keys) | `db`, `query`, `params`, `expect_rows` / `expect_scalar`, `evidence_query` |
| `http` | A GET returns the expected status **and** the body has the required data | `url`, `expect_status`, `json_field` + `op`/`value`, `contains`, `headers` |
| `output` | The job's own stdout/stderr contains (or doesn't contain) a marker, e.g. no `Traceback` | `stream`, `contains` / `not_contains` / `matches` |

`op` is one of `exists, equals, not_equals, non_empty, empty, gt, gte, lt, lte, contains, matches`. `json_field`
takes a dotted path such as `items.0.order_id`.

When a file is missing, the evidence lists the files written in the same directory during the run, so a wrong name
(for example `report-2026-20-11.json` instead of `report-2026-11-20.json`) shows up immediately.

### Code layout

| Module | Role |
|---|---|
| [config.py](src/automation_doctor/config.py) | Load and validate job specs; `${VAR}` substitution (`RUN_DATE`, `JOB_DIR`, `PYTHON`, env, `--var`) |
| [runner.py](src/automation_doctor/runner.py) | Run the command with a timeout, or build a record of a finished run (`inspect`) |
| [checks.py](src/automation_doctor/checks.py) | The four check types; read-only (SQLite opened `mode=ro` + `query_only`, HTTP GET only) |
| [classify.py](src/automation_doctor/classify.py) | Pure function: run record + check results → verdict |
| [evidence.py](src/automation_doctor/evidence.py), [sanitize.py](src/automation_doctor/sanitize.py) | Minimal, bounded, redacted bundle for the diagnosis provider |
| [providers/](src/automation_doctor/providers/) | `DiagnosisProvider` interface; `RulesProvider`; `OllamaProvider`; output validation |
| [report.py](src/automation_doctor/report.py) | Terminal, Markdown and HTML (everything escaped) |
| [demo.py](src/automation_doctor/demo.py) | Runs `demo/jobs/*.json` against a local fake API and measures the results |

## Demo scenarios

All scenarios are synthetic and live in [demo/jobs/](demo/jobs/). The jobs ([demo/scripts/](demo/scripts/)) have an
explicit `--fault` flag that injects the bug.

| Scenario | What the job does | Job exit code | Labelled verdict |
|---|---|---|---|
| 01_healthy_report | Writes today's report correctly | 0 | OK |
| 02_wrong_file | Writes `report-2026-20-11.json` (day/month swapped) | 0 | SILENT_FAILURE |
| 03_http_missing_data | API returns HTTP 200 with `items: []` | 0 | SILENT_FAILURE |
| 04_duplicate_on_retry | Commit succeeds, ack times out, retry inserts the batch again | 0 | SILENT_FAILURE |
| 05_stale_output | Takes a "no new data" early exit; yesterday's file is still there | 0 | SILENT_FAILURE |
| 06_swallowed_error | Logs a traceback, drops a section of the report, exits 0 | 0 | SILENT_FAILURE |
| 07_partial_batch | Silently skips 3 of 10 rows | 0 | SILENT_FAILURE |
| 08_healthy_retry | Same retry as 04, but the loader is idempotent | 0 | OK |
| 09_healthy_api_sync | API returns data; saved locally | 0 | OK |
| 10_reported_failure | Can't open its DB; exits 1 (a visible failure) | 1 | REPORTED_FAILURE |

### Example output (`make demo`, scenario 04)

```
[SILENT_FAILURE] 04_duplicate_on_retry: Reported success, wrong outcome
  reason:   exit code 0, but 1 outcome check(s) failed: no duplicate orders
  schedule: daily 02:00   exit code: 0   duration: 0.049s
  expected: Each of the 10 orders appears exactly once in orders.db.
  checks:
    FAIL no duplicate orders
         expected: query on orders.db: row count == 0
         actual:   row count = 10
         note:     Expected row count == 0
    PASS whole batch of 10 loaded
  diagnosis [rules (heuristics-v1), uncertainty: low]:
    what:  The job exited 0 but the database has more matching rows than expected (row count = 10).
    cause: Non-idempotent write: a retry re-inserted records that had already been committed (log: 'load_orders:
           attempt 1 failed: timed out waiting for commit acknowledgement; retrying').
    fix:   Make the write idempotent: add a UNIQUE constraint on the business key and use INSERT ... ON CONFLICT
           DO NOTHING (or UPSERT), or check-then-insert inside one transaction.  (suggestion only, not applied)
    next:  Inspect the duplicate rows shown in the evidence, then de-duplicate them manually after review.
```

Each run also writes `demo-output/reports/<job>-<run>.html|md|json`, plus `demo-output/index.html` and `summary.json`.

## Measured results

These results come from this repo's own 10 synthetic scenarios. The scenarios and the checks were written by the
same author, so this is **not** a real-world detection rate. Regenerate with `make results` and `make demo-llm`.

- **Verdicts: 10/10 synthetic scenarios classified as labelled (rules-based, deterministic)**, with 0 alerts on the
  3 healthy runs and 0 missed silent failures ([results/demo-rules.md](results/demo-rules.md)).
- **With `qwen2.5-coder:7b` via Ollama:** verdicts are unchanged, because they are deterministic. The model only adds
  a plain-English explanation. Cause identification was not rigorously evaluated. On a crude check (does the
  explanation mention a labelled cause keyword?), the model scored 7/7 and the rules provider 7/7, so
  **the LLM matched the rules provider on cause keyword identification on this set**. Latency was ~48 s median (max 68 s) on an M1 / 16 GB
  under normal desktop load with swap in use
  ([results/demo-ollama-qwen2.5-coder-7b.md](results/demo-ollama-qwen2.5-coder-7b.md)).

Qwen 3.5 9B was **not** measured. On this 16 GB M1, the 7B model was already slow while other apps were running, so
the tested 7B is the default. To try another local model, run `AD_OLLAMA_MODEL=<model> make demo-llm`.

## Add your own cron job

1. Write a spec next to your job, e.g. `~/jobs/backup.json`:

   ```json
   {
     "name": "nightly-backup",
     "schedule": "cron: 30 1 * * *",
     "command": ["/usr/bin/env", "bash", "/Users/me/jobs/backup.sh"],
     "cwd": "/Users/me/jobs",
     "timeout_seconds": 600,
     "expected_outcome": "backups/notes-<date>.sql is written by this run and has a CREATE TABLE statement.",
     "checks": [
       {"type": "file", "name": "backup written today", "path": "backups/notes-${RUN_DATE}.sql",
        "fresh": true, "contains": "CREATE TABLE"},
       {"type": "output", "name": "no errors logged", "stream": "stderr", "not_contains": "ERROR"}
     ]
   }
   ```

   A YAML version is in [examples/nightly_backup.yaml](examples/nightly_backup.yaml) (needs `pip install
   'automation-doctor[yaml]'`). Run `automation-doctor validate ~/jobs/backup.json` to check the spec without running
   anything.

2. **Option A: wrap the job.** Automation Doctor runs the command, then checks it. Replace the crontab line:

   ```cron
   30 1 * * * /path/to/automation-doctor/.venv/bin/automation-doctor run /Users/me/jobs/backup.json --out /Users/me/.automation-doctor/reports >> /Users/me/.automation-doctor/cron.log 2>&1
   ```

   The process exit code is the verdict (0 OK, 1 silent failure, ...), so anything that watches cron exit codes
   alerts on silent failures too.

3. **Option B: leave the job alone and inspect afterwards.** Record the exit code and the start time, then:

   ```bash
   automation-doctor inspect ~/jobs/backup.json --exit-code 0 --started-at 2026-11-20T01:30:00 \
       --stdout-file /tmp/backup.out --stderr-file /tmp/backup.err
   # or: automation-doctor inspect ~/jobs/backup.json --run-record examples/run-record.json
   ```

   `${RUN_DATE}` defaults to the inspected run's start date. Freshness checks need `--started-at`; without it they
   report `error`, not `pass`.

Diagnosis settings, from highest to lowest precedence: the CLI flags `--provider` and `--model`, then the
environment variables `AD_PROVIDER`, `AD_OLLAMA_MODEL` and `AD_OLLAMA_HOST`, then the spec's `diagnosis` block, then
the defaults (`auto`, `qwen2.5-coder:7b`, `127.0.0.1:11434`). `auto` uses Ollama when the model is pulled, and the
rules otherwise. `automation-doctor status` shows which one will be used.

## How untrusted input is handled

Logs, files, HTTP bodies and model replies are treated as data, never as instructions.

- Evidence sent to the model is trimmed (about 6 KB at most) and has ANSI and control characters stripped. Common
  secret patterns (passwords, bearer tokens, AWS/GitHub/`sk-` keys, credentials in URLs) are redacted. The prompt
  wraps it in `<evidence>` tags and says it is untrusted.
- The model has no tools. Its reply must be JSON that matches a fixed schema. Fields are length-capped and
  `uncertainty` must be `low|medium|high`. A malformed reply falls back to the rules provider. Suggestions that look
  destructive (`rm -rf`, `DROP TABLE`, `curl | sh` and so on) get a warning. Nothing is executed.
- The verdict is computed before the model is called. A test sends a hostile model reply ("verdict OK") and checks
  that the verdict and the exit code do not change.
- Ollama must be on a loopback address unless `diagnosis.allow_remote` is set, so evidence stays on the machine.
- All reports are HTML-escaped. SQLite checks open the database read-only. A test shows that a `DELETE` fails even
  when it bypasses the spec validator.

## Limitations

- **The scenarios are synthetic and small.** Ten scenarios, written alongside the checks, show the mechanism works.
  They do not show how well it catches real failures.
- **Detection is only as good as your checks.** Automation Doctor doesn't infer what "correct" means. You state it
  (a file, a row count, a field). Duplicate detection, for example, needs you to write the `GROUP BY ... HAVING`
  query.
- **The rules-based diagnosis covers known patterns** (wrong or missing file, stale file, duplicates, partial batch,
  HTTP 200 with a bad body, swallowed error, visible failure). Anything else gets a generic message with `high`
  uncertainty.
- **LLM explanations are slow and not validated for correctness.** Expect ~48 s median per failing scenario with
  `qwen2.5-coder:7b` on an M1 / 16 GB (up to ~68 s). In testing it reported `low` uncertainty even when its explanation was
  generic or wrong (scenario 10). Treat it as a draft, not a diagnosis.
- Freshness uses file modification time (1 s tolerance). Clock skew or network filesystems can break it.
- Checks run after the job, so another process can change the outcome in between.
- Only file, SQLite, HTTP GET and log checks exist. There are no Postgres, S3 or queue checks, and no authentication
  flows beyond static headers. Put secrets in `${ENV_VARS}`, not in spec files.
- Redaction is pattern-based and will miss some secrets. Review reports before sharing them.
- Not included, by design: scheduling, alerting/notifications, auto-repair, n8n/Airflow integrations, and a web UI.

## Development

```bash
make test                     # pytest; unittest-compatible, needs no model or network
.venv/bin/python -m unittest discover -s tests   # same tests without pytest
```

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the tests and the demo on Ubuntu and macOS with
Python 3.12, with no model available. The plan is in [docs/PLAN.md](docs/PLAN.md).

License: MIT.
