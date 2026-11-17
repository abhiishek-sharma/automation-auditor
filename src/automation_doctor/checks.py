"""Deterministic outcome checks.

Each check returns a ``CheckResult`` with status:
  * ``pass``  – the expected outcome is present
  * ``fail``  – the outcome is missing or wrong (this is what catches "reported success, wrong outcome")
  * ``error`` – the check itself could not be evaluated (bad query, unreachable endpoint, ...)

Checks only read. SQLite databases are opened read-only; HTTP checks only issue GET requests.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .sanitize import clean_obj, clean_text

if TYPE_CHECKING:  # pragma: no cover
    from .runner import RunRecord

PASS, FAIL, ERROR = "pass", "fail", "error"
MTIME_TOLERANCE_S = 1.0
OPS = {"exists", "equals", "not_equals", "non_empty", "empty", "gte", "lte", "gt", "lt", "contains", "matches"}


@dataclass
class CheckResult:
    name: str
    type: str
    status: str
    expected: str
    actual: str
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CheckContext:
    cwd: Path
    run: "RunRecord"


# --------------------------------------------------------------------------- assertions

_MISSING = object()


def get_field(data: Any, path: str) -> Any:
    """Look up a dotted path such as ``data.items.0.id``. Returns ``_MISSING`` if absent."""
    cur = data
    for part in [p for p in path.split(".") if p]:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and re.fullmatch(r"-?\d+", part) and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def describe_expectation(op: str, value: Any = None, subject: str = "value") -> str:
    words = {
        "exists": f"{subject} exists", "non_empty": f"{subject} is non-empty", "empty": f"{subject} is empty",
        "equals": f"{subject} == {value!r}", "not_equals": f"{subject} != {value!r}",
        "gte": f"{subject} >= {value!r}", "lte": f"{subject} <= {value!r}", "gt": f"{subject} > {value!r}",
        "lt": f"{subject} < {value!r}", "contains": f"{subject} contains {value!r}",
        "matches": f"{subject} matches /{value}/",
    }
    return words.get(op, f"{subject} {op} {value!r}")


def assert_value(actual: Any, op: str, value: Any = None) -> bool:
    if op == "exists":
        return actual is not _MISSING
    if actual is _MISSING:
        return False
    if op == "non_empty":
        return actual not in (None, "", [], {})
    if op == "empty":
        return actual in (None, "", [], {})
    if op == "equals":
        return actual == value
    if op == "not_equals":
        return actual != value
    if op in {"gte", "lte", "gt", "lt"}:
        try:
            a, b = float(actual), float(value)
        except (TypeError, ValueError):
            return False
        return {"gte": a >= b, "lte": a <= b, "gt": a > b, "lt": a < b}[op]
    if op == "contains":
        if isinstance(actual, (list, dict)):
            return value in actual
        return str(value) in str(actual)
    if op == "matches":
        return re.search(str(value), str(actual)) is not None
    raise ValueError(f"unknown op {op!r}")


def show(value: Any, limit: int = 300) -> str:
    if value is _MISSING:
        return "<missing>"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = repr(value)
    return clean_text(text, limit, keep="head")


def _validate_op(check: dict[str, Any], key: str = "op") -> str | None:
    op = check.get(key)
    if op not in OPS:
        return f"'{key}' must be one of {sorted(OPS)}"
    if op not in {"exists", "non_empty", "empty"} and "value" not in check:
        return f"op '{op}' needs a 'value'"
    if op == "matches":
        try:
            re.compile(str(check["value"]))
        except re.error as exc:
            return f"invalid regex: {exc}"
    return None


def _assertion(check: dict[str, Any]) -> tuple[str, Any]:
    return check.get("op", "non_empty"), check.get("value")


# --------------------------------------------------------------------------- file check

def _ts(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


class FileCheck:
    """Expected file exists, (optionally) was written during this run, and has the right content."""

    @staticmethod
    def validate(check: dict[str, Any]) -> str | None:
        if not isinstance(check.get("path"), str) or not check["path"]:
            return "'path' is required"
        if "json_field" in check or "op" in check:
            check.setdefault("op", "exists")
            return _validate_op(check)
        return None

    @staticmethod
    def evaluate(check: dict[str, Any], ctx: CheckContext) -> CheckResult:
        raw_path = Path(check["path"]).expanduser()
        path = raw_path if raw_path.is_absolute() else ctx.cwd / raw_path
        name = check["name"]
        fresh = bool(check.get("fresh", False))
        result = CheckResult(name=name, type="file", status=PASS, expected="", actual="")
        exp = [f"file {check['path']} exists"]
        if fresh:
            exp.append("written during this run")
        field_name = check.get("json_field")
        if field_name is not None:
            op, value = _assertion(check)
            exp.append("JSON " + describe_expectation(op, value, field_name))
        elif "contains" in check:
            exp.append(f"text contains {check['contains']!r}")
        result.expected = "; ".join(exp)
        ev: dict[str, Any] = {"path": str(path)}
        result.evidence = ev

        if not path.is_file():
            result.status = FAIL
            result.actual = "file does not exist"
            ev["exists"] = False
            ev["nearby_files"] = _nearby(path, ctx.run.started_at)
            near_written = [f["name"] for f in ev["nearby_files"] if f.get("modified_during_run")]
            result.message = f"Expected file is missing: {path.name}"
            if near_written:
                result.message += f"; files written in the same directory during this run: {', '.join(near_written)}"
            return result

        st = path.stat()
        ev.update(exists=True, size_bytes=st.st_size, modified_at=_ts(st.st_mtime))
        if fresh:
            if ctx.run.started_at is None:
                result.status = ERROR
                result.actual = "file exists, but run start time is unknown"
                result.message = "Freshness cannot be verified without a run start time (pass --started-at)"
                return result
            is_fresh = st.st_mtime + MTIME_TOLERANCE_S >= ctx.run.started_at
            ev["modified_during_run"] = is_fresh
            ev["run_started_at"] = _ts(ctx.run.started_at)
            if not is_fresh:
                result.status = FAIL
                result.actual = f"file is stale: last modified {_ts(st.st_mtime)}, before the run started"
                result.message = "The file was not rewritten by this run"
                return result

        if st.st_size > 5_000_000:
            result.status = ERROR
            result.actual = f"file too large to inspect ({st.st_size} bytes)"
            return result
        text = path.read_text(encoding="utf-8", errors="replace")
        ev["content_head"] = clean_text(text, 800, keep="head")

        if field_name is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                result.status = FAIL
                result.actual = f"file is not valid JSON ({exc.msg} at line {exc.lineno})"
                return result
            op, value = _assertion(check)
            actual = get_field(data, field_name)
            result.actual = f"{field_name} = {show(actual)}"
            if not assert_value(actual, op, value):
                result.status = FAIL
                result.message = f"Field check failed: expected {describe_expectation(op, value, field_name)}"
            return result
        if "contains" in check:
            needle = str(check["contains"])
            found = needle in text
            result.actual = "text found" if found else "text not found"
            if not found:
                result.status = FAIL
                result.message = f"File does not contain {needle!r}"
            return result
        result.actual = f"file exists ({st.st_size} bytes)"
        return result


def _nearby(path: Path, started_at: float | None, limit: int = 15) -> list[dict[str, Any]]:
    """List siblings of a missing file, closest names first, to show where output went instead."""
    parent = path.parent
    if not parent.is_dir():
        return [{"note": f"directory {parent} does not exist"}]
    entries = []
    for child in parent.iterdir():
        if not child.is_file():
            continue
        st = child.stat()
        entry: dict[str, Any] = {"name": child.name, "size_bytes": st.st_size, "modified_at": _ts(st.st_mtime),
                                 "name_similarity": round(difflib.SequenceMatcher(None, child.name, path.name).ratio(), 2)}
        if started_at is not None:
            entry["modified_during_run"] = st.st_mtime + MTIME_TOLERANCE_S >= started_at
        entries.append(entry)
    entries.sort(key=lambda e: (-e.get("modified_during_run", False), -e["name_similarity"]))
    return entries[:limit]


# --------------------------------------------------------------------------- sqlite check

class SqliteCheck:
    """Run a read-only query and assert on the row count or the first value."""

    @staticmethod
    def validate(check: dict[str, Any]) -> str | None:
        if not isinstance(check.get("db"), str) or not check["db"]:
            return "'db' is required"
        if not isinstance(check.get("query"), str) or not check["query"].strip():
            return "'query' is required"
        if not re.match(r"(?is)^\s*(select|with)\b", check["query"]):
            return "'query' must be a SELECT (checks are read-only)"
        if "expect_rows" not in check and "expect_scalar" not in check:
            return "set 'expect_rows' or 'expect_scalar', e.g. {\"op\": \"equals\", \"value\": 1}"
        for key in ("expect_rows", "expect_scalar"):
            if key in check:
                if not isinstance(check[key], dict):
                    return f"'{key}' must be an object"
                problem = _validate_op(check[key])
                if problem:
                    return f"{key}: {problem}"
        return None

    @staticmethod
    def evaluate(check: dict[str, Any], ctx: CheckContext) -> CheckResult:
        raw_path = Path(check["db"]).expanduser()
        db = raw_path if raw_path.is_absolute() else ctx.cwd / raw_path
        result = CheckResult(name=check["name"], type="sqlite", status=PASS, expected="", actual="",
                             evidence={"db": str(db), "query": check["query"]})
        exps = []
        if "expect_rows" in check:
            exps.append(describe_expectation(check["expect_rows"]["op"], check["expect_rows"].get("value"), "row count"))
        if "expect_scalar" in check:
            exps.append(describe_expectation(check["expect_scalar"]["op"], check["expect_scalar"].get("value"),
                                             "first value"))
        result.expected = f"query on {db.name}: " + "; ".join(exps)
        if not db.is_file():
            result.status = FAIL
            result.actual = "database file does not exist"
            return result
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error as exc:
            result.status, result.actual = ERROR, f"cannot open database: {exc}"
            return result
        try:
            conn.execute("PRAGMA query_only = ON")
            cur = conn.execute(check["query"], check.get("params", []))
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(500)
            result.evidence["columns"] = cols
            result.evidence["rows_sample"] = [list(r) for r in rows[:10]]
            result.evidence["row_count"] = len(rows)
            if check.get("evidence_query"):
                ecur = conn.execute(check["evidence_query"], check.get("evidence_params", []))
                result.evidence["evidence_query"] = check["evidence_query"]
                result.evidence["evidence_columns"] = [d[0] for d in ecur.description or []]
                result.evidence["evidence_rows"] = [list(r) for r in ecur.fetchmany(20)]
        except sqlite3.Error as exc:
            result.status, result.actual = ERROR, f"query failed: {exc}"
            return result
        finally:
            conn.close()

        actual_parts, failed = [], []
        if "expect_rows" in check:
            e = check["expect_rows"]
            actual_parts.append(f"row count = {len(rows)}")
            if not assert_value(len(rows), e["op"], e.get("value")):
                failed.append(describe_expectation(e["op"], e.get("value"), "row count"))
        if "expect_scalar" in check:
            e = check["expect_scalar"]
            scalar = rows[0][0] if rows and rows[0] else _MISSING
            actual_parts.append(f"first value = {show(scalar)}")
            result.evidence["scalar"] = None if scalar is _MISSING else scalar
            if not assert_value(scalar, e["op"], e.get("value")):
                failed.append(describe_expectation(e["op"], e.get("value"), "first value"))
        result.actual = "; ".join(actual_parts)
        if failed:
            result.status = FAIL
            result.message = "Expected " + " and ".join(failed)
        return result


# --------------------------------------------------------------------------- http check

class HttpCheck:
    """GET a URL; assert on status and (optionally) a JSON field or substring of the body."""

    @staticmethod
    def validate(check: dict[str, Any]) -> str | None:
        url = check.get("url")
        if not isinstance(url, str) or not re.match(r"(?i)^https?://", url):
            return "'url' must be an http(s) URL"
        if "json_field" in check:
            check.setdefault("op", "exists")
            return _validate_op(check)
        return None

    @staticmethod
    def evaluate(check: dict[str, Any], ctx: CheckContext) -> CheckResult:
        url = check["url"]
        expect_status = int(check.get("expect_status", 200))
        result = CheckResult(name=check["name"], type="http", status=PASS, expected="", actual="",
                             evidence={"url": clean_text(url, 300)})
        exp = [f"GET {clean_text(url, 200)} returns HTTP {expect_status}"]
        field_name = check.get("json_field")
        if field_name is not None:
            exp.append("JSON " + describe_expectation(check["op"], check.get("value"), field_name))
        elif "contains" in check:
            exp.append(f"body contains {check['contains']!r}")
        result.expected = "; ".join(exp)

        headers = {"User-Agent": "automation-doctor/0.1", "Accept": "application/json, */*"}
        headers.update({str(k): str(v) for k, v in (check.get("headers") or {}).items()})
        req = urllib.request.Request(url, headers=headers, method="GET")
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=float(check.get("timeout_seconds", 10))) as resp:
                status, body = resp.status, resp.read(1_000_000)
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            status, body, ctype = exc.code, exc.read(1_000_000), exc.headers.get("Content-Type", "")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            result.status = ERROR
            result.actual = f"request failed: {clean_text(getattr(exc, 'reason', exc), 200)}"
            return result
        text = body.decode("utf-8", errors="replace")
        result.evidence.update(status=status, content_type=clean_text(ctype, 100),
                               latency_ms=round((time.monotonic() - t0) * 1000), body_head=clean_text(text, 1200, keep="head"))
        actual = [f"HTTP {status}"]
        if status != expect_status:
            result.status = FAIL
            result.actual = actual[0]
            result.message = f"Expected HTTP {expect_status}"
            return result
        if field_name is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                result.status, result.actual = FAIL, f"HTTP {status}, body is not JSON"
                return result
            value = get_field(data, field_name)
            actual.append(f"{field_name} = {show(value)}")
            if not assert_value(value, check["op"], check.get("value")):
                result.status = FAIL
                result.message = (f"HTTP {status} but response failed: "
                                  f"{describe_expectation(check['op'], check.get('value'), field_name)}")
        elif "contains" in check:
            found = str(check["contains"]) in text
            actual.append("text found" if found else "text not found")
            if not found:
                result.status = FAIL
                result.message = f"HTTP {status} but body does not contain {check['contains']!r}"
        result.actual = ", ".join(actual)
        return result


# --------------------------------------------------------------------------- output check

class OutputCheck:
    """Assert on the job's own stdout/stderr (e.g. 'rows written: N', no 'WARNING')."""

    @staticmethod
    def validate(check: dict[str, Any]) -> str | None:
        if check.get("stream", "stdout") not in {"stdout", "stderr"}:
            return "'stream' must be stdout or stderr"
        keys = [k for k in ("contains", "not_contains", "matches") if k in check]
        if len(keys) != 1:
            return "set exactly one of 'contains', 'not_contains', 'matches'"
        if keys[0] == "matches":
            try:
                re.compile(str(check["matches"]))
            except re.error as exc:
                return f"invalid regex: {exc}"
        return None

    @staticmethod
    def evaluate(check: dict[str, Any], ctx: CheckContext) -> CheckResult:
        stream = check.get("stream", "stdout")
        text = getattr(ctx.run, stream)
        result = CheckResult(name=check["name"], type="output", status=PASS, expected="", actual="",
                             evidence={"stream": stream})
        if "contains" in check:
            ok = str(check["contains"]) in text
            result.expected = f"{stream} contains {check['contains']!r}"
        elif "not_contains" in check:
            ok = str(check["not_contains"]) not in text
            result.expected = f"{stream} does not contain {check['not_contains']!r}"
        else:
            m = re.search(str(check["matches"]), text)
            ok = m is not None
            result.expected = f"{stream} matches /{check['matches']}/"
            if m:
                result.evidence["match"] = clean_text(m.group(0), 200)
        result.actual = "as expected" if ok else "not as expected"
        if not ok:
            result.status = FAIL
            result.evidence["tail"] = clean_text(text, 800)
        return result


CHECK_TYPES: dict[str, Any] = {"file": FileCheck, "sqlite": SqliteCheck, "http": HttpCheck, "output": OutputCheck}


def run_checks(checks: list[dict[str, Any]], ctx: CheckContext) -> list[CheckResult]:
    results = []
    for check in checks:
        evaluator: Callable[[dict[str, Any], CheckContext], CheckResult] = CHECK_TYPES[check["type"]].evaluate
        try:
            res = evaluator(check, ctx)
        except Exception as exc:  # a broken check must never crash the doctor
            res = CheckResult(name=check.get("name", "?"), type=check["type"], status=ERROR, expected="",
                              actual=f"check crashed: {type(exc).__name__}: {clean_text(exc, 200)}")
        res.evidence = clean_obj(res.evidence)
        results.append(res)
    return results
