"""Command line interface. Exit codes: 0 OK, 1 SILENT_FAILURE, 2 REPORTED_FAILURE, 3 UNVERIFIED, 4 usage/config error."""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_job
from .doctor import evaluate, save
from .providers import OllamaProvider, ProviderUnavailable, resolve_settings
from .report import render_markdown, render_terminal
from .runner import inspect_run, parse_time, run_job

CONFIG_ERROR_EXIT = 4


def _vars(pairs: list[str]) -> dict[str, str]:
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise ConfigError(f"--var expects NAME=VALUE, got {p!r}")
        k, v = p.split("=", 1)
        out[k] = v
    return out


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("job", help="path to the job spec (.json, or .yaml with PyYAML installed)")
    p.add_argument("--var", action="append", default=[], metavar="NAME=VALUE", help="set a ${NAME} variable")
    p.add_argument("--run-date", help="value for ${RUN_DATE} (default: today, YYYY-MM-DD)")
    p.add_argument("--provider", choices=["auto", "ollama", "rules", "none"],
                   help="diagnosis provider (default: job spec, AD_PROVIDER, or auto)")
    p.add_argument("--model", help="Ollama model (default: job spec, AD_OLLAMA_MODEL, or qwen2.5-coder:7b)")
    p.add_argument("--out", default=".automation-doctor/reports", help="where reports are written")
    p.add_argument("--no-save", action="store_true", help="do not write report files")
    p.add_argument("--format", choices=["text", "json", "md"], default="text", help="stdout format")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="automation-doctor", description=__doc__)
    ap.add_argument("--version", action="version", version=f"automation-doctor {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="run the job's command, then check its outcome")
    _common(p)

    p = sub.add_parser("inspect", help="check the outcome of a run that already happened (e.g. from cron)")
    _common(p)
    p.add_argument("--run-record", help="JSON file: {exit_code, started_at, finished_at, stdout|stdout_file, ...}")
    p.add_argument("--exit-code", type=int, help="the run's exit code")
    p.add_argument("--stdout-file")
    p.add_argument("--stderr-file")
    p.add_argument("--started-at", help="ISO-8601 or epoch seconds; needed for freshness checks")
    p.add_argument("--finished-at")

    p = sub.add_parser("validate", help="validate a job spec without running anything")
    p.add_argument("job")
    p.add_argument("--var", action="append", default=[])

    p = sub.add_parser("demo", help="run the bundled demo scenarios and write reports")
    p.add_argument("--provider", choices=["auto", "ollama", "rules", "none"], default="rules",
                   help="diagnosis provider for the demo (default: rules, so it needs no model)")
    p.add_argument("--model", help="Ollama model when --provider is ollama/auto")
    p.add_argument("--out", default="demo-output", help="output directory (wiped first)")
    p.add_argument("--results-file", help="also write the measured results table (Markdown) here")
    p.add_argument("--only", action="append", help="run only these scenario names")

    p = sub.add_parser("status", help="show whether the local Ollama model is available")
    p.add_argument("--model")
    return ap


def _read(path: str | None) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace") if path else ""


def _emit(result, args) -> int:
    d = result.to_dict()
    if not args.no_save:
        paths = save(result, Path(args.out))
        d["report_files"] = {k: str(v) for k, v in paths.items()}
    if args.format == "json":
        print(json.dumps(d, indent=2, default=str))
    elif args.format == "md":
        print(render_markdown(d))
    else:
        print(render_terminal(d, color=sys.stdout.isatty()))
        if not args.no_save:
            print(f"  report:   {d['report_files']['html']}")
    return result.verdict.exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "demo":
            from .demo import run_demo
            return run_demo(provider=args.provider, model=args.model, out=Path(args.out),
                            results_file=args.results_file, only=args.only)
        if args.cmd == "status":
            cfg = resolve_settings(load_default_cfg(), model=args.model)
            try:
                OllamaProvider(model=cfg.model, host=cfg.host).check_available()
                print(f"ollama: available, model {cfg.model} is pulled ({cfg.host})")
                return 0
            except (ProviderUnavailable, ValueError) as exc:
                print(f"ollama: unavailable: {exc}\nchecks and rule-based diagnosis still work.")
                return 1
        if args.cmd == "validate":
            job = load_job(args.job, variables=_vars(args.var))
            print(f"ok: {job.name}: {len(job.checks)} check(s): " + ", ".join(c["type"] for c in job.checks))
            return 0

        rec = json.loads(_read(args.run_record)) if args.cmd == "inspect" and args.run_record else {}
        started = parse_time(args.started_at or rec.get("started_at")) if args.cmd == "inspect" else None
        run_date = args.run_date
        if run_date is None and started is not None:  # inspecting a past run: ${RUN_DATE} is that run's date
            run_date = _dt.date.fromtimestamp(started).isoformat()
        job = load_job(args.job, variables=_vars(args.var), run_date=run_date)
        diag = resolve_settings(job.diagnosis, provider=args.provider, model=args.model)
        if args.cmd == "run":
            run = run_job(job)
        else:
            exit_code = args.exit_code if args.exit_code is not None else rec.get("exit_code")
            if exit_code is None:
                raise ConfigError("inspect needs --exit-code or a run record with exit_code")
            base = Path(args.run_record).parent if args.run_record else Path.cwd()
            def rec_file(key: str) -> str:
                return _read(str(base / rec[key])) if rec.get(key) else ""
            run = inspect_run(
                job, exit_code=int(exit_code),
                stdout=_read(args.stdout_file) if args.stdout_file else rec.get("stdout", "") or rec_file("stdout_file"),
                stderr=_read(args.stderr_file) if args.stderr_file else rec.get("stderr", "") or rec_file("stderr_file"),
                started_at=started,
                finished_at=parse_time(args.finished_at or rec.get("finished_at")))
        return _emit(evaluate(job, run, diag), args)
    except (ConfigError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"automation-doctor: error: {exc}", file=sys.stderr)
        return CONFIG_ERROR_EXIT


def load_default_cfg():
    from .config import DiagnosisConfig
    return DiagnosisConfig()
