"""Helpers for handling untrusted text (logs, file contents, HTTP bodies, model output).

Everything that crosses a trust boundary passes through ``clean_text`` before it is
stored in a report, shown to a user, or sent to a model.
"""
from __future__ import annotations

import re
from typing import Any

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# Control characters other than \n and \t (includes \r, NUL, bell, bidi overrides).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization)"
                r"(\"?\s*[:=]\s*\"?)[^\s\"',;]+"), r"\1\2[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
]


def strip_control(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CTRL_RE.sub("", text)


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def truncate(text: str, limit: int, *, keep: str = "tail") -> str:
    """Trim ``text`` to ``limit`` characters, keeping the head or the tail."""
    if limit <= 0 or len(text) <= limit:
        return text
    marker = f"[...{len(text) - limit} chars trimmed...]"
    if keep == "head":
        return text[:limit] + "\n" + marker
    return marker + "\n" + text[-limit:]


def clean_text(value: Any, limit: int = 2000, *, keep: str = "tail") -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return truncate(redact(strip_control(str(value))), limit, keep=keep)


def clean_obj(obj: Any, limit: int = 600, depth: int = 0) -> Any:
    """Recursively clean an evidence structure (dicts, lists, scalars)."""
    if depth > 6:
        return "[nested too deep]"
    if isinstance(obj, dict):
        return {clean_text(k, 80): clean_obj(v, limit, depth + 1) for k, v in list(obj.items())[:40]}
    if isinstance(obj, (list, tuple)):
        items = [clean_obj(v, limit, depth + 1) for v in list(obj)[:25]]
        if len(obj) > 25:
            items.append(f"[...{len(obj) - 25} more items]")
        return items
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return clean_text(obj, limit, keep="head")
