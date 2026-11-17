"""Run a job command, or build a record of a run that already happened."""
from __future__ import annotations

import datetime as _dt
import os
import shlex
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Job
from .sanitize import clean_text

STORED_OUTPUT_CHARS = 16_000


@dataclass
class RunRecord:
    job_name: str
    run_id: str
    mode: str  # "run" or "inspect"
    command: str
    cwd: str
    started_at: float | None
    finished_at: float | None
    exit_code: int | None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    stdout_chars: int = 0
    stderr_chars: int = 0
    launch_error: str = ""

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return round(self.finished_at - self.started_at, 3)

    @property
    def reported_success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.launch_error

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_seconds"] = self.duration_seconds
        d["started_at_iso"] = iso(self.started_at)
        d["finished_at_iso"] = iso(self.finished_at)
        return d


def iso(ts: float | None) -> str:
    if ts is None:
        return ""
    return _dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def new_run_id() -> str:
    return _dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def resolve_cwd(job: Job) -> Path:
    base = Path(job.source_path).resolve().parent if job.source_path else Path.cwd()
    cwd = Path(job.cwd).expanduser()
    return cwd if cwd.is_absolute() else (base / cwd).resolve()


def run_job(job: Job) -> RunRecord:
    cwd = resolve_cwd(job)
    cwd.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(job.env)
    env.setdefault("RUN_DATE", job.variables.get("RUN_DATE", ""))
    if isinstance(job.command, str) and not job.shell:
        argv: list[str] | str = shlex.split(job.command)
    else:
        argv = job.command
    if job.shell and isinstance(argv, list):
        argv = shlex.join(argv)

    record = RunRecord(job_name=job.name, run_id=new_run_id(), mode="run", command=job.command_display,
                       cwd=str(cwd), started_at=time.time(), finished_at=None, exit_code=None)
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, shell=job.shell, capture_output=True,
                              timeout=job.timeout_seconds, stdin=subprocess.DEVNULL)
        record.exit_code = proc.returncode
        out, err = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        record.timed_out = True
        out, err = exc.stdout or b"", exc.stderr or b""
    except OSError as exc:
        record.launch_error = f"{type(exc).__name__}: {exc}"
        out, err = b"", b""
    record.finished_at = time.time()
    _store_output(record, out, err)
    return record


def inspect_run(job: Job, *, exit_code: int, stdout: str = "", stderr: str = "",
                started_at: float | None = None, finished_at: float | None = None) -> RunRecord:
    """Build a record for a run that already happened (e.g. from cron logs)."""
    record = RunRecord(job_name=job.name, run_id=new_run_id(), mode="inspect", command=job.command_display,
                       cwd=str(resolve_cwd(job)), started_at=started_at, finished_at=finished_at,
                       exit_code=exit_code)
    _store_output(record, stdout.encode(), stderr.encode())
    return record


def _store_output(record: RunRecord, out: bytes, err: bytes) -> None:
    out_s = out.decode("utf-8", errors="replace")
    err_s = err.decode("utf-8", errors="replace")
    record.stdout_chars, record.stderr_chars = len(out_s), len(err_s)
    record.stdout = clean_text(out_s, STORED_OUTPUT_CHARS)
    record.stderr = clean_text(err_s, STORED_OUTPUT_CHARS)


def parse_time(value: str | None) -> float | None:
    """Accept an epoch number or an ISO-8601 timestamp."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    dt = _dt.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()
