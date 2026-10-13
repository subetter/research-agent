"""Redact API keys from logs and traces. Langfuse (when wired) should reuse these names."""

from __future__ import annotations

import os
import re

SENSITIVE_SETTING_FIELDS = (
    "siliconflow_api_key",
    "embedding_api_key",
    "llm_api_key",
    "tavily_api_key",
)

SENSITIVE_ENV_NAMES = (
    "SILICONFLOW_API_KEY",
    "EMBEDDING_API_KEY",
    "LLM_API_KEY",
    "TAVILY_API_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_PUBLIC_KEY",
)

_BEARER_RE = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)


def secret_values(settings=None) -> list[str]:
    found: list[str] = []
    for name in SENSITIVE_ENV_NAMES:
        value = os.environ.get(name) or ""
        if len(value) >= 4:
            found.append(value)
    if settings is not None:
        for field in SENSITIVE_SETTING_FIELDS:
            value = getattr(settings, field, "") or ""
            if len(value) >= 4:
                found.append(value)
    return list(dict.fromkeys(found))


def redact_text(text: str, settings=None) -> str:
    """Replace configured secrets and Bearer tokens. Never raise."""
    if not text:
        return text
    out = str(text)
    for secret in secret_values(settings):
        out = out.replace(secret, "[REDACTED]")
    return _BEARER_RE.sub(r"\1[REDACTED]", out)


def langfuse_mask_keys() -> dict[str, str]:
    """Field names a Langfuse client should treat as secrets."""
    return {name: "[REDACTED]" for name in (*SENSITIVE_ENV_NAMES, *SENSITIVE_SETTING_FIELDS)}
