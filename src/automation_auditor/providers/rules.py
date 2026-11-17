"""Deterministic, offline diagnosis heuristics.

These explain the most common silent-failure patterns from check evidence alone. They do not decide the
verdict (``classify`` does); they only explain it. They also serve as the baseline the LLM is compared with.
"""
from __future__ import annotations

import re
from typing import Any

from .base import Diagnosis

_RETRY_RE = re.compile(r"(?i)\bretr(y|ying|ied)\b|attempt\s*\d")
_ERRORISH_RE = re.compile(r"(?i)(fatal|traceback|exception|error|cannot|warn(ing)?|timeout|timed out|partial|failed)")
_EXC_LINE_RE = re.compile(r"^\s*[A-Za-z_][\w.]*(Error|Exception|Timeout)\b:?.*$", re.M)


def _first_line_matching(text: str, pattern: re.Pattern[str]) -> str:
    for line in text.splitlines():
        if pattern.search(line):
            return line.strip()[:200]
    return ""


class RulesProvider:
    name = "rules"

    def diagnose(self, bundle: dict[str, Any]) -> Diagnosis:
        run = bundle.get("run", {})
        logs = f"{run.get('stdout_tail', '')}\n{run.get('stderr_tail', '')}"
        checks = bundle.get("failed_or_errored_checks", [])
        verdict = bundle.get("verdict")

        if verdict == "REPORTED_FAILURE":
            line = (_last_exception(logs) or _first_line_matching(run.get("stderr_tail", ""), _ERRORISH_RE)
                    or _first_line_matching(logs, _ERRORISH_RE) or "no error line found in the log tail")
            return self._d(f"The job reported failure ({bundle.get('verdict_reason')}).",
                           f"See the job's own error output: {line}",
                           "Fix the error the job reported, then re-run it under automation-auditor.",
                           "Read the stderr tail in this report.", "medium",
                           "The job's own exit status is the primary evidence; outcome checks are secondary.")

        for c in checks:
            ev = c.get("evidence", {}) or {}
            if c["type"] == "file" and ev.get("exists") is False:
                written = [f for f in ev.get("nearby_files", []) if isinstance(f, dict) and f.get("modified_during_run")]
                expected_name = str(ev.get("path", "")).rsplit("/", 1)[-1]
                if written:
                    best = max(written, key=lambda f: f.get("name_similarity", 0))
                    return self._d(
                        f"The job exited 0 but did not write {expected_name}. During this run it wrote "
                        f"{best['name']} in the same directory instead.",
                        f"Output path or filename is built wrongly (wrote {best['name']}, expected {expected_name}); "
                        "check the date format string or path template.",
                        f"Correct the filename template so it produces {expected_name}, and make the job fail "
                        "(non-zero exit) if the expected file is not present after writing.",
                        f"Compare the code that builds the output path with the expected name {expected_name}.",
                        "low" if best.get("name_similarity", 0) >= 0.7 else "medium",
                        "A file with a similar name was written during the run.")
                return self._d(f"The job exited 0 but the expected file {expected_name} was not written.",
                               "The job skipped its write step, or wrote to another directory.",
                               "Make the job verify its output exists before exiting 0.",
                               "Check the job's working directory and output path settings.", "high",
                               "No file was written nearby during the run, so the destination is unknown.")
            if c["type"] == "file" and ev.get("modified_during_run") is False:
                return self._d("The expected file exists but was not rewritten by this run (stale output).",
                               "The job exited early or wrote nothing new, and an old file from an earlier run "
                               "is still there.",
                               "Write output atomically (temp file then rename) and fail if nothing was produced.",
                               "Check the job's logs for an early-exit branch.", "medium",
                               "The file's modified time comes before the run start.")
            if c["type"] == "sqlite":
                scalar, rows = ev.get("scalar"), ev.get("row_count")
                dup_hint = bool(_RETRY_RE.search(logs)) or "dup" in c["name"].lower()
                more_than_expected = (
                    (isinstance(scalar, (int, float)) and "== " in c["expected"]
                     and _num_after(c["expected"], "== ") is not None and scalar > _num_after(c["expected"], "== "))
                    or (isinstance(rows, int) and rows > 0 and "row count == 0" in c["expected"])
                )
                if more_than_expected or (dup_hint and c["status"] == "fail" and (rows or 0) > 0):
                    retry_line = _first_line_matching(logs, _RETRY_RE)
                    cause = "Non-idempotent write: a retry re-inserted records that had already been committed"
                    if retry_line:
                        cause += f" (log: {retry_line!r})"
                    return self._d(
                        f"The job exited 0 but the database has more matching rows than expected ({c['actual']}).",
                        cause + ".",
                        "Make the write idempotent: add a UNIQUE constraint on the business key and use "
                        "INSERT ... ON CONFLICT DO NOTHING (or UPSERT), or check-then-insert inside one transaction.",
                        "Inspect the duplicate rows shown in the evidence, then de-duplicate them manually "
                        "after review.",
                        "low" if retry_line else "medium",
                        "A retry is visible in the logs." if retry_line else "No retry is visible in the logs.")
                want = _num_after(c["expected"], "== ") or _num_after(c["expected"], ">= ")
                if c["status"] == "fail" and isinstance(scalar, (int, float)) and want and 0 < scalar < want:
                    return self._d(
                        f"The job exited 0 but only {scalar:g} of {want:g} expected records are in the database "
                        "(partial batch).",
                        "Some records were dropped without an error: likely a filter, a validation step that skips "
                        "rows, or a swallowed per-row exception.",
                        "Count input and written records in the job and exit non-zero (or log each rejected row) "
                        "when they differ.",
                        "Diff the source batch against the table to find which records are missing.", "medium",
                        "The counts show the gap; which rows are missing and why is not in the evidence.")
                if c["status"] == "fail":
                    return self._d(f"The job exited 0 but the database does not hold the expected records "
                                   f"({c['actual']}).",
                                   "The job's write was skipped, rolled back, or went to another database.",
                                   "Commit explicitly and make the job fail if its write affected 0 rows.",
                                   "Check the database path the job uses and its transaction handling.", "medium",
                                   "Only the row counts were checked.")
            if c["type"] == "http" and ev.get("status") is not None:
                body = str(ev.get("body_head", ""))
                hint = _first_line_matching(body.replace(",", "\n"), _ERRORISH_RE)
                return self._d(
                    f"The endpoint returned HTTP {ev.get('status')} but the response failed the outcome check "
                    f"({c['actual']}).",
                    "The upstream reported success while sending an empty or partial payload"
                    + (f" (response mentions: {hint})" if hint else "") + "; the job trusts the status code alone.",
                    "Check the response body, not just the status: treat a missing or empty required field as "
                    "a failure (non-zero exit) and retry or alert.",
                    "Check the upstream service's health for this run window.", "medium" if hint else "high",
                    "The body shows the symptom; the upstream root cause is not visible from here.")
            if c["type"] == "output":
                exc_line = _last_exception(logs)
                return self._d(f"The job exited 0 but its own output failed a check: {c['expected']}.",
                               "The job logged a problem (or missed its success marker) but still exited 0; "
                               "it swallowed an error" + (f" ({exc_line})" if exc_line else "") + ".",
                               "Let the error reach the top level (re-raise) so the job exits non-zero.",
                               "Read the log tail in the evidence.", "medium", "Based on log text only.")
            if c["status"] == "error":
                return self._d(f"The outcome check '{c['name']}' could not be evaluated: {c['actual']}.",
                               "The check's configuration or a dependency (endpoint, DB) is broken, not "
                               "necessarily the job.",
                               "Fix the check configuration or make the dependency reachable.",
                               "Run `automation-auditor validate` on the job spec.", "high",
                               "The outcome is unknown.")
        if checks:
            c = checks[0]
            return self._d(f"Outcome check '{c['name']}' failed: expected {c['expected']}, got {c['actual']}.",
                           "No known pattern matched.", "Inspect the evidence below.",
                           "Compare expected with actual output.", "high", "No heuristic matched.")
        return self._d("No problem found.", "n/a", "n/a", "n/a", "low", "All checks passed.")

    def _d(self, diagnosis: str, cause: str, fix: str, step: str, unc: str, why: str) -> Diagnosis:
        return Diagnosis(diagnosis=diagnosis, likely_cause=cause, suggested_fix=fix, next_step=step,
                         uncertainty=unc, uncertainty_reason=why, provider=self.name, model="heuristics-v1",
                         latency_ms=0)


def _last_exception(text: str) -> str:
    found = [m.group(0).strip() for m in _EXC_LINE_RE.finditer(text)]
    return found[-1][:200] if found else ""


def _num_after(text: str, marker: str) -> float | None:
    m = re.search(re.escape(marker) + r"(-?\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None
