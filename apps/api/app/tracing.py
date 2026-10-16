"""Optional Langfuse tracing. Failures here must never break a research or chat run."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .redact import configured_secrets, redact, strip_reasoning

log = logging.getLogger(__name__)
_current_trace: ContextVar[str | None] = ContextVar("langfuse_trace_id", default=None)
_current_parent: ContextVar[Any] = ContextVar("langfuse_parent", default=None)
_TRACING: "Tracing | None" = None


class NullSpan:
    def end(self, **kwargs):
        return None

    def update(self, **kwargs):
        return None

    def span(self, **kwargs):
        return self

    def generation(self, **kwargs):
        return self


class Tracing:
    def __init__(self, settings=None, client=None, enabled: bool | None = None, secrets: list[str] | None = None, host: str = ""):
        self.host = (host or (getattr(settings, "langfuse_host", "") if settings else "")).rstrip("/")
        public = getattr(settings, "langfuse_public_key", "") if settings else ""
        secret = getattr(settings, "langfuse_secret_key", "") if settings else ""
        configured = bool(public and secret and self.host)
        self.enabled = configured if enabled is None else enabled
        self.secrets = list(secrets if secrets is not None else (configured_secrets(settings) if settings else []))
        self.client = client
        if self.enabled and self.client is None:
            try:
                from langfuse import Langfuse
                self.client = Langfuse(public_key=public, secret_key=secret, host=self.host)
            except Exception:
                log.exception("Langfuse client failed to start; tracing stays off")
                self.enabled = False
                self.client = None

    def safe(self, value: Any) -> Any:
        return redact(strip_reasoning(value), self.secrets)

    def trace_url(self, trace_id: str | None) -> str | None:
        if not self.enabled or not trace_id or not self.host:
            return None
        return f"{self.host}/trace/{trace_id}"

    def _trace(self, trace_id: str, name: str | None = None, metadata: dict | None = None):
        if not self.enabled or not self.client or not trace_id:
            return NullSpan()
        payload = {"id": trace_id}
        if name:
            payload["name"] = name
        if metadata:
            payload["metadata"] = self.safe(metadata)
        return self.client.trace(**payload)

    def start(self, trace_id: str, *, name: str, metadata: dict | None = None):
        token = _current_trace.set(trace_id)
        try:
            self._trace(trace_id, name=name, metadata=metadata)
        except Exception:
            log.exception("Langfuse trace start failed")
        return token

    def reset(self, token) -> None:
        try:
            _current_trace.reset(token)
        except Exception:
            _current_trace.set(None)

    def flush(self) -> None:
        if not self.enabled or not self.client:
            return
        try:
            self.client.flush()
        except Exception:
            log.exception("Langfuse flush failed")

    @contextmanager
    def span(self, name: str, *, trace_id: str | None = None, input: Any = None, metadata: dict | None = None):
        tid = trace_id or _current_trace.get()
        if not self.enabled or not tid:
            yield NullSpan()
            return
        token = None
        observation: Any = NullSpan()
        try:
            parent = _current_parent.get()
            factory = parent.span if parent is not None and not isinstance(parent, NullSpan) else self._trace(tid).span
            observation = factory(name=name, input=self.safe(input), metadata=self.safe(metadata or {"trace_id": tid}))
            token = _current_parent.set(observation)
        except Exception:
            log.exception("Langfuse span start failed")
            observation = NullSpan()
        try:
            yield observation
        finally:
            try:
                if token is not None:
                    _current_parent.reset(token)
                observation.end()
            except Exception:
                log.exception("Langfuse span end failed")

    def generation(self, name: str, *, model: str = "", input: Any = None, output: Any = None, usage: dict | None = None, metadata: dict | None = None, level: str | None = None):
        tid = _current_trace.get()
        if not self.enabled or not tid:
            return
        try:
            parent = _current_parent.get()
            target = parent if parent is not None and not isinstance(parent, NullSpan) else self._trace(tid)
            payload = {
                "name": name,
                "model": model or None,
                "input": self.safe(input),
                "output": self.safe(output),
                "metadata": self.safe(metadata or {}),
            }
            if usage:
                payload["usage"] = usage
            if level:
                payload["level"] = level
            target.generation(**payload)
        except Exception:
            log.exception("Langfuse generation failed")

    def tool(self, name: str, *, input: Any = None, output: Any = None, metadata: dict | None = None):
        with self.span(f"tool:{name}", input=input, metadata=metadata) as observation:
            try:
                observation.update(output=self.safe(output))
            except Exception:
                log.exception("Langfuse tool span update failed")

    def score(self, name: str, value: float, *, comment: str | None = None, data_type: str = "NUMERIC"):
        tid = _current_trace.get()
        if not self.enabled or not tid or not self.client:
            return
        try:
            self.client.score(trace_id=tid, name=name, value=value, comment=comment, data_type=data_type)
        except Exception:
            log.exception("Langfuse score failed")

    def update(self, **metadata):
        tid = _current_trace.get()
        if not self.enabled or not tid:
            return
        try:
            self._trace(tid).update(metadata=self.safe(metadata))
        except Exception:
            log.exception("Langfuse trace update failed")


def init_tracing(settings) -> Tracing:
    global _TRACING
    _TRACING = Tracing(settings)
    return _TRACING


def get_tracing() -> Tracing:
    return _TRACING or Tracing(enabled=False)


def reasoning_tokens(usage: dict | None) -> int:
    usage = usage or {}
    details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    for key in ("reasoning_tokens", "reasoning"):
        value = details.get(key, usage.get(key))
        if isinstance(value, int):
            return value
    return 0
