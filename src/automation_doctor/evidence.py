"""Build the minimal, sanitised evidence bundle a diagnosis provider may see."""
from __future__ import annotations

import json
from typing import Any

from .checks import PASS, CheckResult
from .classify import Verdict
from .config import Job
from .runner import RunRecord
from .sanitize import clean_obj, clean_text

MAX_BUNDLE_CHARS = 9000
LOG_TAIL_CHARS = 1500


def build_bundle(job: Job, run: RunRecord, results: list[CheckResult], verdict: Verdict) -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "job": {
            "name": job.name,
            "schedule": clean_text(job.schedule, 120),
            "command": clean_text(job.command_display, 300, keep="head"),
            "expected_outcome": clean_text(job.expected_outcome, 600, keep="head"),
        },
        "run": {
            "mode": run.mode,
            "exit_code": run.exit_code,
            "timed_out": run.timed_out,
            "launch_error": clean_text(run.launch_error, 300),
            "duration_seconds": run.duration_seconds,
            "stdout_tail": clean_text(run.stdout, LOG_TAIL_CHARS),
            "stderr_tail": clean_text(run.stderr, LOG_TAIL_CHARS),
        },
        "verdict": verdict.code,
        "verdict_reason": verdict.reason,
        "failed_or_errored_checks": [
            {"name": r.name, "type": r.type, "status": r.status, "expected": r.expected, "actual": r.actual,
             "message": r.message, "evidence": clean_obj(r.evidence, 400)}
            for r in results if r.status != PASS
        ],
        "passed_checks": [r.name for r in results if r.status == PASS],
    }
    # Shrink until it fits: logs first, then evidence detail.
    for shrink in (lambda b: _shrink_logs(b, 600), lambda b: _shrink_logs(b, 200), _shrink_evidence):
        if len(json.dumps(bundle, default=str)) <= MAX_BUNDLE_CHARS:
            break
        shrink(bundle)
    return bundle


def _shrink_logs(bundle: dict[str, Any], limit: int) -> None:
    for key in ("stdout_tail", "stderr_tail"):
        bundle["run"][key] = clean_text(bundle["run"][key], limit)


def _shrink_evidence(bundle: dict[str, Any]) -> None:
    for check in bundle["failed_or_errored_checks"]:
        check["evidence"] = clean_obj(check["evidence"], 120)
