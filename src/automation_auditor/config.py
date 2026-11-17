"""Job spec loading and validation.

A job spec is a JSON file (or YAML, if PyYAML is installed) describing a command, a schedule
label, the expected outcome in plain words, and one or more outcome checks.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .checks import CHECK_TYPES

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    pass


@dataclass
class DiagnosisConfig:
    provider: str = "auto"  # auto | ollama | rules | none
    model: str = "qwen2.5-coder:7b"
    host: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 180.0
    allow_remote: bool = False


@dataclass
class Job:
    name: str
    command: list[str] | str
    checks: list[dict[str, Any]]
    schedule: str = ""
    expected_outcome: str = ""
    description: str = ""
    cwd: str = "."
    shell: bool = False
    timeout_seconds: float = 300.0
    env: dict[str, str] = field(default_factory=dict)
    diagnosis: DiagnosisConfig = field(default_factory=DiagnosisConfig)
    source_path: str = ""
    variables: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def command_display(self) -> str:
        if isinstance(self.command, str):
            return self.command
        return " ".join(self.command)


def _read_spec(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ConfigError(
                f"{path}: YAML specs need PyYAML (pip install pyyaml); JSON works with no dependencies"
            ) from exc
        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be an object")
    return data


def builtin_variables(spec_path: Path, run_date: str | None = None) -> dict[str, str]:
    return {
        "JOB_DIR": str(spec_path.resolve().parent),
        "RUN_DATE": run_date or _dt.date.today().isoformat(),
        "PYTHON": sys.executable,
        "HOME": str(Path.home()),
        "WORKSPACE": os.getcwd(),
    }


def substitute(value: Any, variables: dict[str, str], where: str = "spec") -> Any:
    """Replace ``${NAME}`` in every string of ``value``. Unknown names are an error."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            name = m.group(1)
            if name in variables:
                return variables[name]
            if name in os.environ:
                return os.environ[name]
            raise ConfigError(f"{where}: unknown variable ${{{name}}} (pass --var {name}=...)")
        return _VAR_RE.sub(repl, value)
    if isinstance(value, list):
        return [substitute(v, variables, where) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, variables, f"{where}.{k}") for k, v in value.items()}
    return value


def load_job(path: str | os.PathLike[str], *, variables: dict[str, str] | None = None,
             run_date: str | None = None) -> Job:
    spec_path = Path(path)
    if not spec_path.is_file():
        raise ConfigError(f"job spec not found: {spec_path}")
    raw = _read_spec(spec_path)
    vars_ = builtin_variables(spec_path, run_date)
    vars_.update(variables or {})
    data = substitute(raw, vars_, spec_path.name)
    return job_from_dict(data, source_path=str(spec_path), variables=vars_)


def job_from_dict(data: dict[str, Any], *, source_path: str = "", variables: dict[str, str] | None = None) -> Job:
    where = source_path or "job"
    name = data.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        raise ConfigError(f"{where}: 'name' must be 1-64 chars of letters, digits, '.', '_' or '-'")

    command = data.get("command")
    if isinstance(command, list):
        if not command or not all(isinstance(c, str) for c in command):
            raise ConfigError(f"{where}: 'command' list must contain strings")
    elif not isinstance(command, str) or not command.strip():
        raise ConfigError(f"{where}: 'command' must be a string or a list of strings")

    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ConfigError(f"{where}: 'checks' must be a non-empty list")
    for i, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ConfigError(f"{where}: checks[{i}] must be an object")
        ctype = check.get("type")
        if ctype not in CHECK_TYPES:
            raise ConfigError(f"{where}: checks[{i}].type must be one of {sorted(CHECK_TYPES)}, got {ctype!r}")
        check.setdefault("name", f"{ctype} check {i + 1}")
        problem = CHECK_TYPES[ctype].validate(check)
        if problem:
            raise ConfigError(f"{where}: checks[{i}] ({check['name']}): {problem}")

    diag_raw = data.get("diagnosis") or {}
    if not isinstance(diag_raw, dict):
        raise ConfigError(f"{where}: 'diagnosis' must be an object")
    diagnosis = DiagnosisConfig(
        provider=str(diag_raw.get("provider", "auto")),
        model=str(diag_raw.get("model", DiagnosisConfig.model)),
        host=str(diag_raw.get("host", DiagnosisConfig.host)),
        timeout_seconds=float(diag_raw.get("timeout_seconds", DiagnosisConfig.timeout_seconds)),
        allow_remote=bool(diag_raw.get("allow_remote", False)),
    )
    if diagnosis.provider not in {"auto", "ollama", "rules", "none"}:
        raise ConfigError(f"{where}: diagnosis.provider must be auto, ollama, rules or none")

    env = data.get("env") or {}
    if not isinstance(env, dict):
        raise ConfigError(f"{where}: 'env' must be an object")

    known = {"name", "command", "checks", "schedule", "expected_outcome", "description", "cwd", "shell",
             "timeout_seconds", "env", "diagnosis"}
    return Job(
        name=name,
        command=command,
        checks=checks,
        schedule=str(data.get("schedule", "")),
        expected_outcome=str(data.get("expected_outcome", "")),
        description=str(data.get("description", "")),
        cwd=str(data.get("cwd", ".")),
        shell=bool(data.get("shell", False)),
        timeout_seconds=float(data.get("timeout_seconds", 300)),
        env={str(k): str(v) for k, v in env.items()},
        diagnosis=diagnosis,
        source_path=source_path,
        variables=dict(variables or {}),
        extra={k: v for k, v in data.items() if k not in known},
    )
