#!/usr/bin/env python3
"""Run the workbench graph and single-search baseline. Default is offline replay."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from app.config import Settings
from app.engine import Engine, coverage_matrix, validate_claims, verification_note
from app.providers import BudgetExceeded, Provider, is_report_operation
from app.store import Store
from live_eval import (
    LiveEvalConfigError,
    build_sample_rows,
    estimate_cost,
    guess_subject,
    live_out_dir,
    live_settings,
    preflight_live,
    price_table,
    render_cost_summary,
    require_live_keys,
    resolve_task_ids,
    summarize_cost,
    task_by_id,
    usage_from_store,
    write_samples_csv,
)
from score import score_dir, summarize

FIXTURES = ROOT / "fixtures"
REPEATS = 3


def load_fixture(task_id: str) -> dict:
    dest = FIXTURES / task_id
    return {
        "task": json.loads((dest / "task.json").read_text(encoding="utf-8")),
        "tavily": json.loads((dest / "tavily.json").read_text(encoding="utf-8")),
        "model": json.loads((dest / "model.json").read_text(encoding="utf-8")),
        "policies": json.loads((dest / "policies.json").read_text(encoding="utf-8")),
    }


def list_tasks() -> list[str]:
    return json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))


def settings_for(path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=str(path),
        research_mode="live",
        llm_api_key="offline-eval",
        llm_model="offline-eval",
        tavily_api_key="offline-eval",
        max_model_calls=80,
        report_reserved_calls=4,
        max_search_calls=24,
        max_gap_rounds=1,
    )


def attach_live_metrics(payload: dict, store, run_id: str, started: float, prices: dict, task=None, settings=None) -> dict:
    usage = usage_from_store(store, run_id)
    payload["usage"] = usage["usage"]
    payload["model_calls"] = usage["model_calls"]
    payload["search_calls"] = usage["search_calls"]
    payload["prompt_tokens"] = usage["prompt_tokens"]
    payload["completion_tokens"] = usage["completion_tokens"]
    payload["reasoning_tokens"] = usage["reasoning_tokens"]
    payload["tokens"] = usage["tokens"]
    payload["wall_seconds"] = round(time.perf_counter() - started, 3)
    payload["cost"] = estimate_cost(
        usage["prompt_tokens"],
        usage["completion_tokens"],
        usage["reasoning_tokens"],
        usage["search_calls"],
        prices,
        total_tokens=usage["tokens"],
    )
    ceiling = int(getattr(settings, "eval_max_tokens_per_task", 0) or 0) if settings else 0
    payload["token_ceiling"] = ceiling or None
    payload["token_ceiling_hit"] = bool(ceiling and usage["tokens"] >= ceiling)
    if not payload.get("coverage") and task:
        plan = (store.run(run_id) or {}).get("plan") or {}
        payload["coverage"] = coverage_matrix(
            {"subjects": plan.get("subjects") or task.get("subjects") or [], "dimensions": plan.get("dimensions") or task.get("dimensions") or []},
            payload.get("evidence") or store.evidence(run_id),
        )
    return payload


def bind_policy_claims(policy_claims, evidence):
    out = []
    for claim in policy_claims:
        subject = claim["subject"]
        dimension = claim["dimension"]
        matches = []
        for item in evidence:
            if not (item.get("task_key") or "").startswith(f"research:{subject}:"):
                continue
            dims = item.get("dimensions") or []
            blob = f"{item.get('title') or ''} {item.get('quote') or ''}"
            if dimension in dims or dimension in blob:
                matches.append(item)
        if not matches:
            matches = [item for item in evidence if (item.get("task_key") or "").startswith(f"research:{subject}:")]
        ids = [] if claim.get("kind") == "unknown" else [item["id"] for item in matches[:2]]
        out.append({**claim, "evidence_ids": ids})
    return out


def install_replay(provider: Provider, fixtures: dict, system: str):
    store = provider.store
    model = fixtures["model"]
    tavily = fixtures["tavily"]
    policies = fixtures["policies"][system]

    async def banned_complete(*args, **kwargs):
        raise RuntimeError("offline eval must not call the model HTTP client")

    async def replay_chat(run_id, messages, key, tools=None):
        cached = store.operation(run_id, key)
        if cached is not None:
            return cached
        limit = provider.settings.max_model_calls
        if not is_report_operation(key):
            limit -= min(provider.settings.report_reserved_calls, limit - 1)
        if not store.reserve(run_id, key, "model", limit):
            raise BudgetExceeded(f"模型调用达到阶段上限（{limit} 次），或该调用尚未确认结果；已有证据保留。")
        if key == "synthesize":
            claims = bind_policy_claims(policies["claims"], store.evidence(run_id))
            result = {"role": "assistant", "content": json.dumps({"claims": claims, "summary": f"{system} offline"}, ensure_ascii=False)}
            tokens = 40
        elif key == "verify":
            result = {"role": "assistant", "content": json.dumps({"results": policies["verify"]}, ensure_ascii=False)}
            tokens = 20
        elif key in model:
            payload = dict(model[key])
            tokens = int(payload.pop("_tokens", 8))
            result = payload
        elif key.startswith("research-model:"):
            result = {"role": "assistant", "content": "没有更多工具调用"}
            tokens = 4
        else:
            store.settle(run_id, key, status="unknown")
            raise KeyError(f"missing pinned model response for {key}")
        store.save_operation(run_id, key, result)
        store.settle(run_id, key, tokens)
        store.event(run_id, "model.finished", {"operation": key, "tokens": tokens})
        return result

    async def replay_search(run, query, key, limit):
        cached = store.operation(run["id"], key)
        if cached is not None:
            return cached
        if not store.reserve(run["id"], key, "search", limit):
            return {"results": [], "budget_exhausted": True}
        pack = tavily.get(query) or {"results": []}
        store.save_operation(run["id"], key, pack)
        store.settle(run["id"], key)
        return pack

    import app.providers as providers_mod
    providers_mod.complete = banned_complete
    provider.chat = replay_chat
    provider.search = replay_search


async def wait_status(store, run_id, statuses, timeout=60):
    async with asyncio.timeout(timeout):
        while True:
            run = store.run(run_id)
            if run["status"] in statuses:
                return run
            await asyncio.sleep(0.02)


def export_payload(task, system, repeat, run, store, error=None):
    usage = store.query(
        "SELECT kind,status,COUNT(*) AS calls,SUM(tokens) AS tokens FROM usage WHERE run_id=? GROUP BY kind,status",
        (run["id"],),
    )
    return {
        "task_id": task["id"],
        "family": task["family"],
        "system": system,
        "repeat": repeat,
        "status": run.get("status"),
        "mode": run.get("mode"),
        "error": error or run.get("error"),
        "artifact": run.get("artifact"),
        "evidence": store.evidence(run["id"]),
        "usage": usage,
        "coverage": run.get("coverage") or (run.get("artifact") or {}).get("coverage"),
    }


async def run_workbench(fixtures: dict, workdir: Path, repeat: int) -> dict:
    task = fixtures["task"]
    settings = settings_for(workdir)
    store = Store(workdir / "business.sqlite")
    async with AsyncSqliteSaver.from_conn_string(str(workdir / "graph.sqlite")) as saver:
        engine = Engine(settings, store, saver)
        install_replay(engine.provider, fixtures, "workbench")
        project = store.create_project("offline-eval", "")
        request = {"question": task["question"], "subjects": task["subjects"], "dimensions": task["dimensions"]}
        run = store.create_run(project["id"], task["question"], "live", {"request": request})
        if run["mode"] != "live":
            raise RuntimeError("offline eval must run live mode with pinned HTTP, not demo")
        engine.schedule(run["id"], {"run_id": run["id"], "request": request})
        ready = await wait_status(store, run["id"], {"waiting_input", "failed"})
        if ready["status"] != "waiting_input":
            payload = export_payload(task, "workbench", repeat, ready, store)
            await engine.shutdown()
            store.close()
            return payload
        store.update(run["id"], status="running")
        engine.schedule(run["id"], await engine.resume_input(run["id"]))
        done = await wait_status(store, run["id"], {"completed", "partial", "failed"})
        payload = export_payload(task, "workbench", repeat, done, store)
        await engine.shutdown()
    store.close()
    return payload


async def run_baseline(fixtures: dict, workdir: Path, repeat: int) -> dict:
    task = fixtures["task"]
    settings = settings_for(workdir)
    store = Store(workdir / "business.sqlite")
    provider = Provider(settings, store)
    install_replay(provider, fixtures, "baseline")
    project = store.create_project("offline-eval", "")
    request = {"question": task["question"], "subjects": task["subjects"], "dimensions": task["dimensions"]}
    run = store.create_run(project["id"], task["question"], "live", {"request": request})
    plan = {"subjects": task["subjects"], "dimensions": task["dimensions"], "max_search_calls": settings.max_search_calls, "max_gap_rounds": 0}

    async def gate():
        return None

    query = task["baseline_query"]
    search = await provider.search(run, query, "baseline-search", settings.max_search_calls)
    page_subjects = task.get("page_subjects") or {}
    for page in search.get("results") or []:
        subject = page_subjects.get(page["url"]) or task["subjects"][0]
        store.add_evidence(project["id"], run["id"], f"research:{subject}:0", {
            "title": page.get("title") or page["url"],
            "url": page["url"],
            "quote": page.get("snippet") or "",
            "locator": "snippet:1",
            "source_type": "web",
            "dimensions": [],
        })
    message = await provider.chat(run["id"], [], "synthesize")
    bundle = json.loads(message["content"])
    verified = await provider.verify(run, plan, bundle, store.evidence(run["id"]), gate)
    evidence = store.evidence(run["id"])
    claims = validate_claims(plan, verified, evidence)
    coverage = coverage_matrix(plan, evidence)
    unknown = sum(claim["kind"] == "unknown" for claim in claims)
    artifact = {
        "title": task["question"],
        "summary": verified.get("summary", "baseline A"),
        "claims": claims,
        "mode": "live",
        "verification_note": verification_note("live"),
        "coverage": coverage,
        "metrics": {
            "claims": len(claims),
            "evidence": len(evidence),
            "unknown": unknown,
            "with_references": sum(bool(claim["evidence_ids"]) for claim in claims),
            "cells_covered": coverage["covered"],
            "cells_total": coverage["total"],
        },
    }
    store.update(run["id"], artifact=artifact, status="partial" if unknown else "completed", error=None)
    payload = export_payload(task, "baseline", repeat, store.run(run["id"]), store)
    store.close()
    return payload


async def run_live_workbench(task: dict, workdir: Path, repeat: int, source) -> dict:
    settings = live_settings(workdir, source)
    prices = price_table(settings)
    store = Store(workdir / "business.sqlite")
    started = time.perf_counter()
    timeout = settings.run_timeout_seconds + 60
    async with AsyncSqliteSaver.from_conn_string(str(workdir / "graph.sqlite")) as saver:
        engine = Engine(settings, store, saver)
        project = store.create_project("live-eval", "")
        request = {"question": task["question"], "subjects": task["subjects"], "dimensions": task["dimensions"]}
        run = store.create_run(project["id"], task["question"], "live", {"request": request})
        if run["mode"] != "live":
            raise RuntimeError("live eval must not fall back to demo")
        engine.schedule(run["id"], {"run_id": run["id"], "request": request})
        ready = await wait_status(store, run["id"], {"waiting_input", "failed"}, timeout=timeout)
        if ready["status"] != "waiting_input":
            payload = attach_live_metrics(export_payload(task, "workbench", repeat, ready, store), store, run["id"], started, prices, task=task, settings=settings)
            await engine.shutdown()
            store.close()
            return payload
        plan = {**(store.run(run["id"]).get("plan") or {}), "subjects": task["subjects"], "dimensions": task["dimensions"]}
        store.update(run["id"], status="running", plan=plan)
        engine.schedule(run["id"], await engine.resume_input(run["id"]))
        done = await wait_status(store, run["id"], {"completed", "partial", "failed"}, timeout=timeout)
        payload = attach_live_metrics(export_payload(task, "workbench", repeat, done, store), store, run["id"], started, prices, task=task, settings=settings)
        await engine.shutdown()
    store.close()
    return payload


async def run_live_baseline(task: dict, workdir: Path, repeat: int, source) -> dict:
    settings = live_settings(workdir, source)
    prices = price_table(settings)
    store = Store(workdir / "business.sqlite")
    provider = Provider(settings, store)
    project = store.create_project("live-eval", "")
    request = {"question": task["question"], "subjects": task["subjects"], "dimensions": task["dimensions"]}
    run = store.create_run(project["id"], task["question"], "live", {"request": request})
    plan = {"subjects": task["subjects"], "dimensions": task["dimensions"], "max_search_calls": settings.max_search_calls, "max_gap_rounds": 0}
    started = time.perf_counter()

    async def gate():
        return None

    search = await provider.search(run, task["baseline_query"], "baseline-search", settings.max_search_calls)
    for page in search.get("results") or []:
        subject = guess_subject(page, task["subjects"])
        blob = f"{page.get('title') or ''} {page.get('snippet') or ''}"
        from app.providers import tag_text_dimensions
        store.add_evidence(project["id"], run["id"], f"research:{subject}:0", {
            "title": page.get("title") or page["url"],
            "url": page["url"],
            "quote": page.get("snippet") or "",
            "locator": "snippet:1",
            "source_type": "web",
            "dimensions": tag_text_dimensions(blob, task["dimensions"]),
        })
    bundle = await provider.synthesize(run, plan, store.evidence(run["id"]), gate)
    verified = await provider.verify(run, plan, bundle, store.evidence(run["id"]), gate)
    evidence = store.evidence(run["id"])
    claims = validate_claims(plan, verified, evidence)
    coverage = coverage_matrix(plan, evidence)
    unknown = sum(claim["kind"] == "unknown" for claim in claims)
    artifact = {
        "title": task["question"],
        "summary": verified.get("summary", "baseline A"),
        "claims": claims,
        "mode": "live",
        "verification_note": verification_note("live"),
        "coverage": coverage,
        "metrics": {
            "claims": len(claims),
            "evidence": len(evidence),
            "unknown": unknown,
            "with_references": sum(bool(claim["evidence_ids"]) for claim in claims),
            "cells_covered": coverage["covered"],
            "cells_total": coverage["total"],
        },
    }
    store.update(run["id"], artifact=artifact, status="partial" if unknown else "completed", error=None)
    payload = attach_live_metrics(export_payload(task, "baseline", repeat, store.run(run["id"]), store), store, run["id"], started, prices, task=task, settings=settings)
    store.close()
    return payload


def write_payload(dest: Path, payload: dict) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{payload['system']}-{payload['task_id']}-r{payload['repeat']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def run_all(out_dir: Path, repeats: int = REPEATS, task_ids: list[str] | None = None) -> list[dict]:
    payloads = []
    for task_id in resolve_task_ids(task_ids, live=False):
        fixtures = load_fixture(task_id)
        fixtures["task"].setdefault("page_subjects", {})
        for repeat in range(1, repeats + 1):
            payloads.append(await run_workbench(fixtures, out_dir / "work" / f"{task_id}-w{repeat}", repeat))
            write_payload(out_dir, payloads[-1])
            payloads.append(await run_baseline(fixtures, out_dir / "work" / f"{task_id}-b{repeat}", repeat))
            write_payload(out_dir, payloads[-1])
    return payloads


async def run_live(out_dir: Path, repeats: int, task_ids: list[str], source) -> list[dict]:
    require_live_keys(source)
    payloads = []
    for task_id in task_ids:
        task = task_by_id(task_id)
        for repeat in range(1, repeats + 1):
            payloads.append(await run_live_workbench(task, out_dir / "work" / f"{task_id}-w{repeat}", repeat, source))
            write_payload(out_dir, payloads[-1])
            payloads.append(await run_live_baseline(task, out_dir / "work" / f"{task_id}-b{repeat}", repeat, source))
            write_payload(out_dir, payloads[-1])
    return payloads


def write_results_md(table: str, summary: dict, n: int) -> str:
    def fmt(system, key):
        value = (summary.get(system) or {}).get(key)
        if value is None:
            return "n/a"
        if isinstance(value, float):
            return f"{value:.2f}"
        return str(value)

    lines = [
        "# 离线评测结果",
        "",
        "固定 10 题、每题重复 3 次，均值包含全部重复，不取最好一次。失败单独列出，不丢弃。",
        "工作台跑完整研究图（含计划中断、read_source、缺口补充、综合、核验）。",
        "基线 A 只做一次搜索 + 一次综合，搜索摘要入库且不打维度，不跑研究图。",
        "两边同一预算：`max_model_calls=80`、`report_reserved_calls=4`、`max_search_calls=24`。",
        "引用支持率来自核验节点标签（fully/partial），不是人工复标。demo 语义支持率记为 n/a。",
        "冲突三题的结论是 analysis，没有 fact，引用完整率对这两列是 n/a，不编造。",
        "",
        f"导出产物 {n} 份。",
        "",
        f"- 工作台：覆盖 {fmt('workbench', 'coverage_rate')}，引用完整 {fmt('workbench', 'citation_completeness')}，引用支持 {fmt('workbench', 'citation_support')}，模型 {fmt('workbench', 'model_calls')}，搜索 {fmt('workbench', 'search_calls')}，Token {fmt('workbench', 'tokens')}",
        f"- 基线 A：覆盖 {fmt('baseline', 'coverage_rate')}，引用完整 {fmt('baseline', 'citation_completeness')}，引用支持 {fmt('baseline', 'citation_support')}，模型 {fmt('baseline', 'model_calls')}，搜索 {fmt('baseline', 'search_calls')}，Token {fmt('baseline', 'tokens')}",
        "",
        "```",
        table,
        "```",
        "",
    ]
    return "\n".join(lines)


def write_live_md(table: str, summary: dict, cost: dict, n: int) -> str:
    lines = [
        "# 联网评测结果",
        "",
        "可选联网模式。题面与离线集相同，但搜索与模型走真实服务，不使用夹具里的原文或回放主张。",
        "工作台与基线 A 同一预算：`max_model_calls=80`、`report_reserved_calls=4`、`max_search_calls=24`。",
        f"费用为估价（价表 {cost.get('price_version')}，日期 {cost.get('as_of')}），不是账单。",
        cost.get("disclaimer") or "",
        "",
        f"导出产物 {n} 份。",
        "",
        render_cost_summary(cost),
        "",
        "```",
        table,
        "```",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="走真实模型与 Tavily；缺密钥直接拒绝，不回落演示")
    parser.add_argument("--tasks", nargs="+", default=None, help="按题号挑选；联网未指定时默认竞品+缺失+冲突各一题")
    parser.add_argument("--out", default=None)
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--write-fixtures", action="store_true")
    args = parser.parse_args()
    live = bool(args.live)
    repeats = args.repeats if args.repeats is not None else (1 if live else REPEATS)
    try:
        task_ids = resolve_task_ids(args.tasks, live=live)
    except LiveEvalConfigError as exc:
        raise SystemExit(str(exc)) from exc
    if live:
        source = Settings()
        try:
            require_live_keys(source)
        except LiveEvalConfigError as exc:
            raise SystemExit(str(exc)) from exc
        out_dir = Path(args.out) if args.out else live_out_dir(ROOT)
        print(f"联网评测 {len(task_ids)} 题 × {repeats} 次，输出 {out_dir}", flush=True)
        if args.tasks is None:
            print("未指定 --tasks，冒烟默认：comp-chatgpt-gemini（竞品） miss-inkbot-price（缺失） conf-price（冲突）", flush=True)
        try:
            shapes = asyncio.run(preflight_live(source))
        except LiveEvalConfigError as exc:
            raise SystemExit(str(exc)) from exc
        print("联网预检通过：" + "、".join(shapes), flush=True)
        payloads = asyncio.run(run_live(out_dir, repeats, task_ids, source))
        rows, table = score_dir(out_dir)
        print(table)
        prices = price_table(source)
        cost = summarize_cost(payloads, prices)
        print(render_cost_summary(cost))
        samples = build_sample_rows(payloads)
        write_samples_csv(out_dir / "samples.csv", samples)
        summary = summarize(rows)
        (out_dir / "summary.json").write_text(
            json.dumps({"summary": summary, "cost": cost, "n": len(payloads), "tasks": task_ids, "live": True}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / "LIVE.md").write_text(write_live_md(table, summary, cost, len(payloads)), encoding="utf-8")
        return
    if args.write_fixtures or not (FIXTURES / "index.json").exists():
        from build_fixtures import write_fixtures
        write_fixtures()
    out_dir = Path(args.out) if args.out else ROOT / "results" / "latest"
    payloads = asyncio.run(run_all(out_dir, repeats, task_ids))
    rows, table = score_dir(out_dir)
    print(table)
    summary = summarize(rows)
    (out_dir / "summary.json").write_text(json.dumps({"summary": summary, "n": len(payloads)}, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "RESULTS.md").write_text(write_results_md(table, summary, len(payloads)), encoding="utf-8")


if __name__ == "__main__":
    main()
