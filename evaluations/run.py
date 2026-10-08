#!/usr/bin/env python3
"""Run the workbench graph and single-search baseline on pinned fixtures. No API keys."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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


def write_payload(dest: Path, payload: dict) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{payload['system']}-{payload['task_id']}-r{payload['repeat']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def run_all(out_dir: Path, repeats: int = REPEATS, task_ids: list[str] | None = None) -> list[dict]:
    payloads = []
    for task_id in task_ids or list_tasks():
        fixtures = load_fixture(task_id)
        fixtures["task"].setdefault("page_subjects", {})
        for repeat in range(1, repeats + 1):
            payloads.append(await run_workbench(fixtures, out_dir / "work" / f"{task_id}-w{repeat}", repeat))
            write_payload(out_dir, payloads[-1])
            payloads.append(await run_baseline(fixtures, out_dir / "work" / f"{task_id}-b{repeat}", repeat))
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "results" / "latest"))
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--write-fixtures", action="store_true")
    args = parser.parse_args()
    if args.write_fixtures or not (FIXTURES / "index.json").exists():
        from build_fixtures import write_fixtures
        write_fixtures()
    out_dir = Path(args.out)
    payloads = asyncio.run(run_all(out_dir, args.repeats))
    rows, table = score_dir(out_dir)
    print(table)
    summary = summarize(rows)
    (out_dir / "summary.json").write_text(json.dumps({"summary": summary, "n": len(payloads)}, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "RESULTS.md").write_text(write_results_md(table, summary, len(payloads)), encoding="utf-8")


if __name__ == "__main__":
    main()
