"""Persist LLM/tool execution records with redaction and size caps."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .secrets import redact_payload
from .store import now, uid

PREVIEW_CHARS = 8_000
FULL_CHARS = 200_000
TRUNCATE_MARK = "已截断"


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _cap(value, limit: int) -> tuple[object, bool]:
    text = _dump(value)
    if len(text) <= limit:
        return value, False
    if isinstance(value, str):
        return value[: max(0, limit - 8)] + f"…{TRUNCATE_MARK}", True
    if isinstance(value, list):
        kept = []
        size = 2
        for item in value:
            piece = _dump(item)
            if size + len(piece) + 1 > limit:
                kept.append({"_truncated": True, "marker": TRUNCATE_MARK, "omitted": len(value) - len(kept)})
                return kept, True
            kept.append(item)
            size += len(piece) + 1
        return kept, True
    if isinstance(value, dict):
        clipped = dict(value)
        clipped["_truncated"] = True
        clipped["_marker"] = TRUNCATE_MARK
        text = _dump(clipped)
        if len(text) <= limit:
            return clipped, True
        return {"_truncated": True, "marker": TRUNCATE_MARK, "keys": list(value.keys())[:20]}, True
    return {"_truncated": True, "marker": TRUNCATE_MARK}, True


def sanitize_record_payload(payload: dict, settings=None) -> tuple[dict, dict, bool]:
    clean = redact_payload(payload or {}, settings)
    full, full_cut = _cap(clean, FULL_CHARS)
    preview, preview_cut = _cap(full, PREVIEW_CHARS)
    return preview, full, bool(full_cut or preview_cut)


def record_usage(usage: dict | None) -> dict:
    usage = usage or {}
    return {
        "tokens_in": int(usage.get("prompt_tokens") or 0),
        "tokens_out": int(usage.get("completion_tokens") or 0),
        "tokens_total": int(usage.get("total_tokens") or usage.get("prompt_tokens") or 0) + (0 if usage.get("total_tokens") else int(usage.get("completion_tokens") or 0)),
    }


def begin_record(store, run_id: str, *, kind: str, operation_key: str, stage: str, name: str, role: str = "", subject: str = "", settings=None) -> str:
    existing = store.execution_record_by_key(run_id, operation_key)
    if existing:
        return existing["id"]
    record_id = uid("rec")
    store.upsert_execution_record({
        "id": record_id,
        "run_id": run_id,
        "operation_key": operation_key,
        "kind": kind,
        "stage": stage,
        "subject": subject,
        "name": name,
        "role": role,
        "status": "running",
        "started_at": now(),
        "finished_at": None,
        "latency_ms": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_total": 0,
        "preview": {},
        "payload": {},
        "truncated": False,
    })
    store.event(run_id, "record.upserted", {"record_id": record_id, "kind": kind, "name": name, "status": "running", "stage": stage, "subject": subject})
    return record_id


def finish_record(store, run_id: str, record_id: str, *, status: str, preview: dict, payload: dict, usage: dict | None = None, error: str | None = None, settings=None, started_at: str | None = None, kind: str | None = None, name: str | None = None, stage: str | None = None):
    usage_fields = record_usage(usage)
    if usage_fields["tokens_total"] == 0:
        usage_fields["tokens_total"] = usage_fields["tokens_in"] + usage_fields["tokens_out"]
    body = dict(payload or {})
    if error:
        body["error"] = error
    packed = {"preview": preview or {}, **body}
    preview_body, full_body, truncated = sanitize_record_payload(packed, settings)
    finished = now()
    latency = None
    if started_at:
        try:
            begin = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            latency = max(0, int((end - begin).total_seconds() * 1000))
        except ValueError:
            latency = None
    store.upsert_execution_record({
        "id": record_id,
        "run_id": run_id,
        "status": status,
        "finished_at": finished,
        "latency_ms": latency,
        **usage_fields,
        "preview": preview_body,
        "payload": full_body,
        "truncated": truncated,
    })
    store.event(run_id, "record.upserted", {
        "record_id": record_id,
        "kind": kind,
        "name": name,
        "stage": stage,
        "status": status,
        "truncated": truncated,
        "latency_ms": latency,
        "tokens_total": usage_fields["tokens_total"],
    })
    return record_id


def write_record(store, run_id: str, *, kind: str, operation_key: str, stage: str, name: str, role: str = "", subject: str = "", status: str = "succeeded", preview: dict | None = None, payload: dict | None = None, usage: dict | None = None, error: str | None = None, settings=None, latency_ms: int | None = 0) -> str:
    record_id = begin_record(store, run_id, kind=kind, operation_key=operation_key, stage=stage, name=name, role=role, subject=subject, settings=settings)
    current = store.execution_record(record_id) or {}
    finish_record(
        store,
        run_id,
        record_id,
        status=status,
        preview=preview or {},
        payload=payload or {},
        usage=usage,
        error=error,
        settings=settings,
        started_at=current.get("started_at"),
        kind=kind,
        name=name,
        stage=stage,
    )
    if latency_ms is not None:
        store.upsert_execution_record({"id": record_id, "run_id": run_id, "latency_ms": latency_ms})
    return record_id


def public_record(row: dict, *, include_payload: bool = False) -> dict:
    if not row:
        return {}
    preview = row.get("preview") if isinstance(row.get("preview"), dict) else {}
    item = {
        "id": row["id"],
        "run_id": row["run_id"],
        "kind": row.get("kind"),
        "stage": row.get("stage"),
        "subject": row.get("subject"),
        "name": row.get("name"),
        "role": row.get("role"),
        "status": row.get("status"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "latency_ms": row.get("latency_ms"),
        "tokens_in": row.get("tokens_in") or 0,
        "tokens_out": row.get("tokens_out") or 0,
        "tokens_total": row.get("tokens_total") or 0,
        "truncated": bool(row.get("truncated")),
        "preview": preview,
    }
    if include_payload:
        item["payload"] = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return item
