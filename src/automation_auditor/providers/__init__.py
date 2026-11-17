"""Diagnosis providers. The deterministic verdict never depends on these."""
from __future__ import annotations

import os
from typing import Any

from ..config import DiagnosisConfig
from .base import (Diagnosis, DiagnosisProvider, InvalidProviderOutput, ProviderUnavailable, parse_model_json,
                   validate_diagnosis)
from .ollama import OllamaProvider
from .rules import RulesProvider

__all__ = ["Diagnosis", "DiagnosisProvider", "OllamaProvider", "RulesProvider", "ProviderUnavailable",
           "InvalidProviderOutput", "parse_model_json", "validate_diagnosis", "diagnose", "resolve_settings"]


def resolve_settings(cfg: DiagnosisConfig, provider: str | None = None, model: str | None = None) -> DiagnosisConfig:
    """Precedence: CLI flag > environment (AD_PROVIDER, AD_OLLAMA_MODEL, AD_OLLAMA_HOST) > job spec > default."""
    return DiagnosisConfig(
        provider=provider or os.environ.get("AD_PROVIDER") or cfg.provider,
        model=model or os.environ.get("AD_OLLAMA_MODEL") or cfg.model,
        host=os.environ.get("AD_OLLAMA_HOST") or cfg.host,
        timeout_seconds=cfg.timeout_seconds,
        allow_remote=cfg.allow_remote,
    )


def diagnose(bundle: dict[str, Any], cfg: DiagnosisConfig) -> Diagnosis | None:
    """Explain a non-OK verdict. Never raises; falls back to the rules provider."""
    if cfg.provider == "none":
        return None
    rules = RulesProvider()
    if cfg.provider == "rules":
        return rules.diagnose(bundle)
    try:
        llm = OllamaProvider(model=cfg.model, host=cfg.host, timeout=cfg.timeout_seconds,
                             allow_remote=cfg.allow_remote)
        llm.check_available()
        return llm.diagnose(bundle)
    except (ProviderUnavailable, InvalidProviderOutput, ValueError) as exc:
        reason = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # defensive: a model problem must never break the report
        reason = f"unexpected {type(exc).__name__}: {exc}"
    d = rules.diagnose(bundle)
    d.fallback_reason = f"LLM diagnosis unavailable ({reason[:300]}); showing rule-based diagnosis"
    return d
