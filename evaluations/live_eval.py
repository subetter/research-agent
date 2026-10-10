"""Live eval helpers. Opt-in only; never print or log API keys."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from catalog import TASKS

SMOKE_TASKS = ("comp-chatgpt-gemini", "miss-inkbot-price", "conf-price")
SAMPLE_COLUMNS = (
    "sample_id",
    "task_id",
    "system",
    "repeat",
    "subject",
    "dimension",
    "claim_text",
    "cited_quote",
    "url",
    "verifier_label",
    "human_label",
)
SAMPLE_LIMIT = 30
LIVE_TASK_FIELDS = ("id", "family", "question", "subjects", "dimensions", "baseline_query")


class LiveEvalConfigError(RuntimeError):
    pass


def catalog_task_ids() -> list[str]:
    return [task["id"] for task in TASKS]


def live_task_view(task: dict) -> dict:
    return {key: task[key] for key in LIVE_TASK_FIELDS if key in task}


def task_by_id(task_id: str) -> dict:
    for task in TASKS:
        if task["id"] == task_id:
            return live_task_view(task)
    raise LiveEvalConfigError(f"未知评测题 {task_id}")


def resolve_task_ids(requested: list[str] | None, *, live: bool) -> list[str]:
    known = catalog_task_ids()
    if not requested:
        return list(SMOKE_TASKS) if live else list(known)
    unknown = [item for item in requested if item not in known]
    if unknown:
        raise LiveEvalConfigError(f"未知评测题：{', '.join(unknown)}。可选：{', '.join(known)}")
    return list(requested)


def require_live_keys(settings) -> None:
    missing = []
    if not getattr(settings, "llm_api_key", ""):
        missing.append("LLM_API_KEY")
    if not getattr(settings, "llm_model", ""):
        missing.append("LLM_MODEL")
    if not getattr(settings, "tavily_api_key", ""):
        missing.append("TAVILY_API_KEY")
    if missing:
        raise LiveEvalConfigError(
            f"联网评测缺少 {', '.join(missing)}，已拒绝启动，不会回落到演示。请写在仓库根目录 .env。"
        )


def live_settings(workdir: Path, source=None):
    from app.config import Settings

    source = source or Settings()
    require_live_keys(source)
    payload = source.model_dump()
    payload.update(
        data_dir=str(workdir),
        research_mode="live",
        max_model_calls=80,
        report_reserved_calls=4,
        max_search_calls=24,
        max_gap_rounds=1,
    )
    return Settings(_env_file=None, **payload)


def price_table(settings) -> dict:
    return {
        "version": getattr(settings, "eval_price_version", "2026-10-10-v1"),
        "as_of": "2026-10-10",
        "disclaimer": "估价，不是账单；按公开标价估算，不保证等于实际扣费。",
        "deepseek_prompt_usd_per_1m": float(getattr(settings, "eval_deepseek_prompt_usd_per_1m", 0.27)),
        "deepseek_completion_usd_per_1m": float(getattr(settings, "eval_deepseek_completion_usd_per_1m", 1.10)),
        "deepseek_reasoning_usd_per_1m": float(getattr(settings, "eval_deepseek_reasoning_usd_per_1m", 1.10)),
        "tavily_search_usd": float(getattr(settings, "eval_tavily_search_usd", 0.008)),
    }


def billable_output_tokens(prompt: int, completion: int, reasoning: int, total: int) -> tuple[int, int]:
    prompt = int(prompt or 0)
    completion = int(completion or 0)
    reasoning = int(reasoning or 0)
    total = int(total or 0)
    if prompt == 0 and completion == 0 and total:
        return 0, total
    if completion:
        return prompt, completion
    return prompt, reasoning


def estimate_cost(prompt_tokens: int, completion_tokens: int, reasoning_tokens: int, search_calls: int, prices: dict, total_tokens: int = 0) -> dict:
    prompt, output = billable_output_tokens(prompt_tokens, completion_tokens, reasoning_tokens, total_tokens)
    prompt_usd = prompt / 1_000_000 * float(prices["deepseek_prompt_usd_per_1m"])
    if completion_tokens or (not prompt_tokens and not completion_tokens and total_tokens):
        output_rate = float(prices["deepseek_completion_usd_per_1m"])
    else:
        output_rate = float(prices["deepseek_reasoning_usd_per_1m"])
    output_usd = output / 1_000_000 * output_rate
    search_usd = int(search_calls or 0) * float(prices["tavily_search_usd"])
    return {
        "prompt_tokens": prompt,
        "output_tokens": output,
        "search_calls": int(search_calls or 0),
        "model_usd": round(prompt_usd + output_usd, 6),
        "search_usd": round(search_usd, 6),
        "estimated_usd": round(prompt_usd + output_usd + search_usd, 6),
        "price_version": prices["version"],
        "disclaimer": prices["disclaimer"],
    }


def usage_from_store(store, run_id: str) -> dict:
    rows = store.query(
        "SELECT kind,status,COUNT(*) AS calls,SUM(tokens) AS tokens FROM usage WHERE run_id=? GROUP BY kind,status",
        (run_id,),
    )
    model_calls = sum(int(row["calls"] or 0) for row in rows if row["kind"] == "model")
    search_calls = sum(int(row["calls"] or 0) for row in rows if row["kind"] == "search")
    total_tokens = sum(int(row["tokens"] or 0) for row in rows)
    events = store.query("SELECT payload FROM events WHERE run_id=? AND type='model.finished'", (run_id,))
    prompt = sum(int((event["payload"] or {}).get("prompt_tokens") or 0) for event in events)
    completion = sum(int((event["payload"] or {}).get("completion_tokens") or 0) for event in events)
    reasoning = sum(int((event["payload"] or {}).get("reasoning_tokens") or 0) for event in events)
    if prompt == 0 and completion == 0 and total_tokens:
        completion = total_tokens
    return {
        "model_calls": model_calls,
        "search_calls": search_calls,
        "tokens": total_tokens,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
        "usage": rows,
    }


def guess_subject(page: dict, subjects: list[str]) -> str:
    blob = f"{page.get('title') or ''} {page.get('url') or ''} {page.get('snippet') or ''}"
    lowered = blob.lower()
    for subject in subjects:
        if subject and (subject in blob or subject.lower() in lowered):
            return subject
    return subjects[0]


def live_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def live_out_dir(root: Path, stamp: str | None = None) -> Path:
    return root / "results" / f"live-{stamp or live_stamp()}"


def build_sample_rows(payloads: list[dict], limit: int = SAMPLE_LIMIT) -> list[dict]:
    rows = []
    ordered = sorted(payloads, key=lambda item: (item.get("task_id") or "", item.get("system") or "", item.get("repeat") or 0))
    for payload in ordered:
        evidence = {item.get("id"): item for item in payload.get("evidence") or [] if item.get("id")}
        claims = ((payload.get("artifact") or {}).get("claims") or [])
        for claim in claims:
            ids = [item for item in (claim.get("evidence_ids") or []) if item in evidence]
            if not ids:
                continue
            cited = evidence[ids[0]]
            rows.append({
                "sample_id": f"s{len(rows) + 1:03d}",
                "task_id": payload.get("task_id") or "",
                "system": payload.get("system") or "",
                "repeat": payload.get("repeat") or 0,
                "subject": claim.get("subject") or "",
                "dimension": claim.get("dimension") or "",
                "claim_text": claim.get("text") or "",
                "cited_quote": cited.get("quote") or "",
                "url": cited.get("url") or "",
                "verifier_label": claim.get("verification") or "",
                "human_label": "",
            })
            if len(rows) >= limit:
                return rows
    return rows


def write_samples_csv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SAMPLE_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in SAMPLE_COLUMNS})
    return path


def read_samples_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def samples_csv_text(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(SAMPLE_COLUMNS))
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in SAMPLE_COLUMNS})
    return buffer.getvalue()


def summarize_cost(payloads: list[dict], prices: dict, catalog_size: int = 10) -> dict:
    by_system: dict[str, list[dict]] = {}
    for payload in payloads:
        by_system.setdefault(payload.get("system") or "unknown", []).append(payload)
    systems = {}
    for name, items in by_system.items():
        usd = [float((item.get("cost") or {}).get("estimated_usd") or 0) for item in items]
        systems[name] = {
            "n": len(items),
            "estimated_usd": round(sum(usd), 6),
            "mean_usd": round(sum(usd) / len(usd), 6) if usd else 0.0,
        }
    tasks = {payload.get("task_id") for payload in payloads}
    total = round(sum(item["estimated_usd"] for item in systems.values()), 6)
    per_task = (total / len(tasks)) if tasks else 0.0
    return {
        "price_version": prices["version"],
        "as_of": prices.get("as_of"),
        "disclaimer": prices["disclaimer"],
        "systems": systems,
        "tasks": sorted(task for task in tasks if task),
        "n_tasks": len(tasks),
        "batch_estimated_usd": total,
        "projected_10_tasks_usd": round(per_task * catalog_size, 6),
        "projection_note": f"按本批 {len(tasks)} 题均值外推 {catalog_size} 题（含工作台与基线，重复次数与本批相同），仍是估价。",
    }


def render_cost_summary(summary: dict) -> str:
    lines = [
        f"费用为估价（价表 {summary.get('price_version')}，日期 {summary.get('as_of')}），不是账单。",
        summary.get("disclaimer") or "",
        f"本批 {summary.get('n_tasks')} 题合计约 ${summary.get('batch_estimated_usd'):.4f}",
    ]
    for name, item in (summary.get("systems") or {}).items():
        lines.append(f"- {name}：n={item['n']} 合计 ${item['estimated_usd']:.4f}，题均 ${item['mean_usd']:.4f}")
    lines.append(f"外推 10 题约 ${summary.get('projected_10_tasks_usd'):.4f}。{summary.get('projection_note')}")
    return "\n".join(line for line in lines if line)
