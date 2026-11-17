"""Run the bundled synthetic scenarios and measure how they were classified.

Each scenario lives in ``demo/jobs/*.json`` and carries a ``demo`` block with its labelled verdict, optional
fixtures to plant before the run, and keywords a correct diagnosis should mention. The keyword hit is a crude
check of diagnosis relevance, not a quality score.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import DiagnosisConfig, load_job
from .doctor import evaluate, save
from .providers import resolve_settings
from .report import render_index_html, render_terminal
from .runner import run_job

DEMO_RUN_DATE = "2026-11-20"  # fixed so the scenarios are reproducible on any day


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def demo_jobs_dir() -> Path:
    env = os.environ.get("AD_DEMO_DIR")
    return Path(env) if env else repo_root() / "demo" / "jobs"


# --------------------------------------------------------------------------- synthetic API

class _ApiHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        q = parse_qs(url.query)
        scenario = (q.get("scenario") or ["ok"])[0]
        date = (q.get("date") or [DEMO_RUN_DATE])[0]
        if url.path != "/api/orders":
            self._send(404, {"error": "not found"})
        elif scenario == "partial":
            # HTTP 200 with an empty result: the upstream failed but reports success.
            self._send(200, {"date": date, "items": [], "status": "ok",
                             "warnings": ["inventory-service timeout after 3000ms; returning cached empty set"]})
        else:
            self._send(200, {"date": date, "status": "ok", "items": [
                {"order_id": "A-1001", "amount": 42.5}, {"order_id": "A-1002", "amount": 17.0},
                {"order_id": "A-1003", "amount": 99.9}]})

    def _send(self, code: int, obj: dict[str, Any]) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class DemoApi:
    def __enter__(self) -> "DemoApi":
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()


# --------------------------------------------------------------------------- runner

def _plant_fixtures(fixtures: list[dict[str, Any]], workspace: Path) -> None:
    for f in fixtures:
        path = workspace / f["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f.get("content", ""), encoding="utf-8")
        age = float(f.get("age_hours", 0)) * 3600
        if age:
            t = time.time() - age
            os.utime(path, (t, t))


def _keyword_hit(diag: dict[str, Any] | None, keywords: list[str]) -> str:
    if not keywords:
        return "n/a"
    if not diag:
        return "no diagnosis"
    text = " ".join(str(diag.get(k, "")) for k in ("diagnosis", "likely_cause", "suggested_fix")).lower()
    return "yes" if any(k.lower() in text for k in keywords) else "no"


def run_demo(provider: str = "rules", model: str | None = None, out: Path = Path("demo-output"),
             results_file: str | None = None, only: list[str] | None = None) -> int:
    out = out.resolve()
    if out.exists():
        if not (out / ".automation-auditor-demo").exists() and any(out.iterdir()):
            raise ValueError(f"{out} exists and is not a previous demo output; choose another --out")
        shutil.rmtree(out)
    workspace, reports = out / "workspace", out / "reports"
    workspace.mkdir(parents=True)
    reports.mkdir()
    (out / ".automation-auditor-demo").write_text("marker: safe to delete\n")

    specs = sorted(demo_jobs_dir().glob("*.json"))
    if only:
        specs = [s for s in specs if s.stem in only]
    diag_cfg = resolve_settings(DiagnosisConfig(), provider=provider, model=model)
    if diag_cfg.provider in {"ollama", "auto"}:
        print(f"NOTE: using local model {diag_cfg.model} for diagnosis. Expect ~48 s median per failing scenario on an "
              "M1 / 16 GB; verdicts do not depend on the model.\n")
    cases: list[dict[str, Any]] = []
    with DemoApi() as api:
        variables = {"WORKSPACE": str(workspace), "DEMO_API_URL": api.url}
        for spec in specs:
            job = load_job(spec, variables=variables, run_date=DEMO_RUN_DATE)
            meta = job.extra.get("demo", {})
            _plant_fixtures(meta.get("fixtures", []), workspace)
            run = run_job(job)
            result = evaluate(job, run, diag_cfg)
            paths = save(result, reports)
            d = result.to_dict()
            print(render_terminal(d) + "\n")
            dg = d["diagnosis"]
            cases.append({
                "job": job.name, "description": job.description,
                "expected_verdict": meta.get("expected_verdict", "?"), "verdict": result.verdict.code,
                "verdict_matches": meta.get("expected_verdict") == result.verdict.code,
                "exit_code_reported": run.exit_code,
                "diagnosis_provider": (dg["provider"] + (f" ({dg['model']})" if dg.get("model") else "")) if dg else "",
                "diagnosis_fallback": bool(dg and dg.get("fallback_reason")),
                "diagnosis_latency_ms": dg.get("latency_ms") if dg else None,
                "uncertainty": dg.get("uncertainty") if dg else None,
                "cause_keyword_hit": _keyword_hit(dg, meta.get("cause_keywords", [])),
                "report_html": str(paths["html"].relative_to(out)),
            })

    n = len(cases)
    expected_ok = [c for c in cases if c["expected_verdict"] == "OK"]
    expected_bad = [c for c in cases if c["expected_verdict"] != "OK"]
    diagnosed = [c for c in cases if c["cause_keyword_hit"] in {"yes", "no"}]
    latencies = sorted(c["diagnosis_latency_ms"] for c in cases if c["diagnosis_latency_ms"])
    metrics = {
        "cases": n,
        "verdicts_correct": sum(c["verdict_matches"] for c in cases),
        "false_alerts": sum(c["verdict"] != "OK" for c in expected_ok),
        "missed_failures": sum(c["verdict"] == "OK" for c in expected_bad),
        "diagnoses": len(diagnosed),
        "cause_keyword_hits": sum(c["cause_keyword_hit"] == "yes" for c in diagnosed),
        "llm_fallbacks": sum(c["diagnosis_fallback"] for c in cases),
        "median_diagnosis_latency_ms": latencies[len(latencies) // 2] if latencies else None,
        "max_diagnosis_latency_ms": latencies[-1] if latencies else None,
    }
    summary = {"generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
               "provider_setting": diag_cfg.provider, "model": diag_cfg.model if diag_cfg.provider != "rules" else "-",
               "run_date": DEMO_RUN_DATE, "metrics": metrics, "cases": cases}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "index.html").write_text(render_index_html(summary), encoding="utf-8")
    table = results_markdown(summary)
    (out / "summary.md").write_text(table, encoding="utf-8")
    if results_file:
        Path(results_file).parent.mkdir(parents=True, exist_ok=True)
        Path(results_file).write_text(table, encoding="utf-8")
    print(table)
    print(f"Open {out / 'index.html'} for the HTML reports.")
    return 0 if metrics["verdicts_correct"] == n else 1


def results_markdown(summary: dict[str, Any]) -> str:
    m = summary["metrics"]
    lines = [
        "# Automation Doctor: demo results", "",
        f"- Generated: {summary['generated_at']} by `automation-auditor demo --provider {summary['provider_setting']}`",
        f"- Diagnosis model: {summary['model']}",
        "- Scope: this repo's own synthetic scenarios (`demo/jobs/`). This is not a real-world detection rate.", "",
        f"**Verdicts:** {m['verdicts_correct']}/{m['cases']} scenarios classified as labelled · "
        f"alerts on healthy runs: {m['false_alerts']} · missed silent failures: {m['missed_failures']}", "",
        f"**Diagnosis keyword check (crude relevance check):** {m['cause_keyword_hits']}/{m['diagnoses']} diagnoses "
        f"mention an expected cause keyword · LLM fallbacks to rules: {m['llm_fallbacks']} · median latency: "
        f"{m['median_diagnosis_latency_ms']} ms · max: {m['max_diagnosis_latency_ms']} ms", "",
        "| Scenario | Job exit code | Expected | Actual | Match | Diagnosis by | Uncertainty | Cause keyword |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in summary["cases"]:
        lines.append(f"| {c['job']} | {c['exit_code_reported']} | {c['expected_verdict']} | {c['verdict']} | "
                     f"{'yes' if c['verdict_matches'] else 'NO'} | {c['diagnosis_provider'] or '-'} | "
                     f"{c['uncertainty'] or '-'} | {c['cause_keyword_hit']} |")
    return "\n".join(lines) + "\n"
