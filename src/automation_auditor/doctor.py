"""End-to-end: run (or inspect) -> outcome checks -> verdict -> optional diagnosis -> stored result."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .checks import CheckContext, CheckResult, run_checks
from .classify import Verdict, classify
from .config import DiagnosisConfig, Job
from .evidence import build_bundle
from .providers import Diagnosis, diagnose
from .runner import RunRecord, resolve_cwd


@dataclass
class Result:
    job: Job
    run: RunRecord
    checks: list[CheckResult]
    verdict: Verdict
    diagnosis: Diagnosis | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": f"automation-auditor {__version__}",
            "job": {"name": self.job.name, "schedule": self.job.schedule, "description": self.job.description,
                    "expected_outcome": self.job.expected_outcome, "command": self.job.command_display,
                    "spec": self.job.source_path},
            "run": self.run.to_dict(),
            "checks": [c.to_dict() for c in self.checks],
            "verdict": {"code": self.verdict.code, "label": self.verdict.label, "reason": self.verdict.reason,
                        "alert": self.verdict.alert, "exit_code": self.verdict.exit_code},
            "diagnosis": self.diagnosis.to_dict() if self.diagnosis else None,
        }


def evaluate(job: Job, run: RunRecord, diag_cfg: DiagnosisConfig) -> Result:
    results = run_checks(job.checks, CheckContext(cwd=resolve_cwd(job), run=run))
    verdict = classify(run, results)  # deterministic; nothing below can change it
    diagnosis = diagnose(build_bundle(job, run, results, verdict), diag_cfg) if verdict.alert else None
    return Result(job=job, run=run, checks=results, verdict=verdict, diagnosis=diagnosis)


def save(result: Result, out_dir: Path) -> dict[str, Path]:
    from .report import render_html, render_markdown

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{result.job.name}-{result.run.run_id}"
    paths = {"json": out_dir / f"{stem}.json", "html": out_dir / f"{stem}.html", "md": out_dir / f"{stem}.md"}
    paths["json"].write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
    paths["html"].write_text(render_html(result.to_dict()), encoding="utf-8")
    paths["md"].write_text(render_markdown(result.to_dict()), encoding="utf-8")
    return paths
