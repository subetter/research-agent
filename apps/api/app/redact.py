"""Strip secrets before anything is sent to an observability backend."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"
HEADER_KEYS = frozenset({
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "proxy-authorization",
})
BODY_KEYS = frozenset({
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "secret",
    "secret_key",
    "access_token",
    "refresh_token",
    "llm_api_key",
    "tavily_api_key",
    "langfuse_secret_key",
    "langfuse_public_key",
})
BEARER = re.compile(r"(?i)(authorization\s*[:=]\s*|bearer\s+)(\S+)")


def configured_secrets(settings) -> list[str]:
    values = [
        getattr(settings, "llm_api_key", ""),
        getattr(settings, "tavily_api_key", ""),
        getattr(settings, "langfuse_secret_key", ""),
        getattr(settings, "langfuse_public_key", ""),
    ]
    extra = getattr(settings, "langfuse_redact", "") or ""
    values.extend(part.strip() for part in extra.split(","))
    return [item for item in values if isinstance(item, str) and len(item.strip()) >= 4]


def _key_name(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def redact(value: Any, secrets: list[str] | None = None) -> Any:
    secrets = [item for item in (secrets or []) if item]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lowered = _key_name(key)
            header = str(key).strip().lower()
            if header in HEADER_KEYS or lowered in BODY_KEYS:
                out[key] = REDACTED
            else:
                out[key] = redact(item, secrets)
        return out
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        text = BEARER.sub(lambda match: f"{match.group(1)}{REDACTED}", value)
        for secret in secrets:
            if secret and secret in text:
                text = text.replace(secret, REDACTED)
        return text
    return value


def strip_reasoning(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_reasoning(item)
            for key, item in value.items()
            if key not in {"reasoning_content", "reasoning", "thinking"}
        }
    if isinstance(value, list):
        return [strip_reasoning(item) for item in value]
    return value
