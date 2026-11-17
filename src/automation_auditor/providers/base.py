"""Diagnosis provider interface and validation of (untrusted) provider output."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from ..sanitize import clean_text

UNCERTAINTY_LEVELS = ("low", "medium", "high")
FIELD_LIMITS = {"diagnosis": 500, "likely_cause": 500, "suggested_fix": 700, "next_step": 400, "uncertainty_reason": 300}

# JSON schema handed to providers that support structured output (e.g. Ollama `format`).
DIAGNOSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string", "description": "One or two sentences: what went wrong, citing evidence."},
        "likely_cause": {"type": "string", "description": "The most likely root cause."},
        "suggested_fix": {"type": "string", "description": "A concrete repair for the job's code or config."},
        "next_step": {"type": "string", "description": "What the operator should check or do next."},
        "uncertainty": {"type": "string", "enum": list(UNCERTAINTY_LEVELS)},
        "uncertainty_reason": {"type": "string", "description": "What evidence is missing or ambiguous."},
    },
    "required": ["diagnosis", "likely_cause", "suggested_fix", "next_step", "uncertainty", "uncertainty_reason"],
}

# Shown as a warning (never executed). The doctor never runs suggestions, but a human might copy them.
_RISKY = re.compile(r"(?i)(rm\s+-rf|\bdrop\s+(table|database)\b|\btruncate\s+table\b|\bdelete\s+from\b(?![^;]*\bwhere\b)"
                    r"|curl[^|\n]*\|\s*(ba|z)?sh|chmod\s+-R\s+777|\bsudo\b|mkfs|:\(\)\s*\{)")


class ProviderUnavailable(RuntimeError):
    """The provider cannot be reached or is not configured; callers fall back to rules."""


class InvalidProviderOutput(ValueError):
    """The provider answered, but not with a valid diagnosis."""


@dataclass
class Diagnosis:
    diagnosis: str
    likely_cause: str
    suggested_fix: str
    next_step: str
    uncertainty: str
    uncertainty_reason: str
    provider: str
    model: str = ""
    latency_ms: int | None = None
    warnings: list[str] = field(default_factory=list)
    fallback_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DiagnosisProvider(Protocol):
    name: str

    def diagnose(self, bundle: dict[str, Any]) -> Diagnosis:  # pragma: no cover - interface
        ...


def parse_model_json(text: str) -> dict[str, Any]:
    """Extract one JSON object from model text (tolerates ``` fences and leading chatter)."""
    if not isinstance(text, str) or not text.strip():
        raise InvalidProviderOutput("empty response")
    text = re.sub(r"(?s)<think>.*?</think>", "", text).strip()
    fenced = re.search(r"(?s)```(?:json)?\s*(\{.*?\})\s*```", text)
    candidate = fenced.group(1) if fenced else text
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise InvalidProviderOutput("no JSON object in response") from None
        try:
            obj = json.loads(candidate[start:end + 1])
        except json.JSONDecodeError as exc:
            raise InvalidProviderOutput(f"malformed JSON: {exc.msg}") from None
    if not isinstance(obj, dict):
        raise InvalidProviderOutput("response JSON is not an object")
    return obj


def validate_diagnosis(obj: dict[str, Any], *, provider: str, model: str = "",
                       latency_ms: int | None = None) -> Diagnosis:
    """Coerce untrusted provider output into a bounded, plain-text Diagnosis."""
    if not isinstance(obj, dict):
        raise InvalidProviderOutput("diagnosis must be an object")
    values: dict[str, str] = {}
    warnings: list[str] = []
    for key, limit in FIELD_LIMITS.items():
        raw = obj.get(key)
        if isinstance(raw, (list, tuple)):
            raw = "; ".join(str(x) for x in raw)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if key == "uncertainty_reason":
                raw = "not given"
            else:
                raise InvalidProviderOutput(f"missing field '{key}'")
        if isinstance(raw, (dict, bool, int, float)):
            raise InvalidProviderOutput(f"field '{key}' must be text")
        text = clean_text(raw, limit, keep="head").strip()
        values[key] = text
    unc = str(obj.get("uncertainty", "")).strip().lower()
    if unc not in UNCERTAINTY_LEVELS:
        warnings.append(f"provider gave invalid uncertainty {clean_text(unc, 30)!r}; treated as 'high'")
        unc = "high"
    if _RISKY.search(values["suggested_fix"] + " " + values["next_step"]):
        warnings.append("suggestion contains a potentially destructive command; review carefully before using it")
    return Diagnosis(**values, uncertainty=unc, provider=provider, model=model, latency_ms=latency_ms,
                     warnings=warnings)
