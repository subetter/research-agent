import asyncio
import logging
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from .providers import Provider
from .scope import apply_plan_defaults, infer_scope, needs_clarify
from .store import infer_dimensions_from_title, normalize_dimensions, subject_from_task_key, uid

log = logging.getLogger(__name__)


class RunPaused(Exception):
    pass


class RunCancelled(Exception):
    pass


class ResearchState(TypedDict, total=False):
    run_id: str
    request: dict
    plan: dict
    results: list[dict]
    round: int
    bundle: dict
    gaps: list
    coverage: dict


def evidence_dimensions(item: dict) -> list[str]:
    dims = normalize_dimensions(item.get("dimensions"))
    return dims or infer_dimensions_from_title(item.get("title") or "")


def coverage_matrix(plan, evidence):
    subjects = list(plan.get("subjects") or [])
    dimensions = list(plan.get("dimensions") or [])
    cells = {(subject, dimension): [] for subject in subjects for dimension in dimensions}
    for item in evidence:
        subject = subject_from_task_key(item.get("task_key") or "")
        if subject is None:
            continue
        for dimension in evidence_dimensions(item):
            if (subject, dimension) in cells:
                cells[(subject, dimension)].append(item["id"])
    gaps = [{"subject": subject, "dimension": dimension} for (subject, dimension), ids in cells.items() if not ids]
    return {
        "subjects": subjects,
        "dimensions": dimensions,
        "cells": [{"subject": subject, "dimension": dimension, "evidence_ids": ids, "covered": bool(ids)} for (subject, dimension), ids in cells.items()],
        "covered": sum(1 for ids in cells.values() if ids),
        "total": len(cells),
        "gaps": gaps,
    }


def research_targets(plan, gaps):
    if not gaps:
        return [(subject, list(plan["dimensions"])) for subject in plan["subjects"]]
    grouped = {}
    for gap in gaps:
        if isinstance(gap, str):
            grouped[gap] = list(plan["dimensions"])
            continue
        subject = gap.get("subject")
        dimension = gap.get("dimension")
        if not subject:
            continue
        grouped.setdefault(subject, [])
        if dimension:
            if dimension not in grouped[subject]:
                grouped[subject].append(dimension)
        else:
            grouped[subject] = list(plan["dimensions"])
    return [(subject, dimensions) for subject, dimensions in grouped.items() if dimensions]


VERIFICATION_VALUES = {"fully", "partial", "contradicted", "unrelated", "reference_checked", "unconfirmed"}


def validate_claims(plan, bundle, evidence):
    valid = {e["id"] for e in evidence}
    seen = set()
    claims = []
    for claim in bundle.get("claims", []):
        pair = (claim["subject"], claim["dimension"])
        if pair in seen or pair[0] not in plan["subjects"] or pair[1] not in plan["dimensions"]:
            continue
        seen.add(pair)
        original = claim.get("evidence_ids", [])
        ids = list(dict.fromkeys(x for x in original if x in valid))
        item = {**claim, "id": uid("claim"), "evidence_ids": ids}
        structural_fail = len(ids) != len(set(original)) or (claim.get("kind") == "fact" and not ids)
        if structural_fail:
            item["kind"] = "unknown"
            item["text"] = "待确认：引用缺失或引用 ID 不合法，原结论未通过结构校验。"
            item["verification"] = "unconfirmed"
        else:
            cited = [e for e in evidence if e.get("id") in ids]
            if item.get("kind") == "fact" and cited and all(e.get("citation_role") == "analysis" for e in cited):
                item["kind"] = "analysis"
            prior = claim.get("verification")
            if item.get("kind") == "unknown":
                item["verification"] = prior if prior in {"unconfirmed", "contradicted", "unrelated"} else "unconfirmed"
            elif prior in VERIFICATION_VALUES:
                item["verification"] = prior
            else:
                item["verification"] = "reference_checked"
        claims.append(item)
    for subject in plan["subjects"]:
        for dimension in plan["dimensions"]:
            if (subject, dimension) not in seen:
                claims.append({"id": uid("claim"), "subject": subject, "dimension": dimension, "text": "未确认：未取得足够证据。", "evidence_ids": [], "kind": "unknown", "verification": "unconfirmed"})
    return claims


def verification_note(mode):
    if mode == "demo":
        return "演示模式只按规则标记引用是否存在（reference_checked），未做摘录是否支持主张的语义判断。"
    return "已对照入库原文摘录判断支持程度（fully / partial / contradicted / unrelated）；不被原文支持的事实降为待确认。不能保证结论为真，仍需人工抽查。"


class Engine:
    def __init__(self, settings, store, checkpointer):
        self.settings = settings
        self.store = store
        self.provider = Provider(settings, store)
        self.tasks = {}
        self.run_slots = asyncio.Semaphore(2)
        graph = StateGraph(ResearchState)
        graph.add_node("clarify", self.clarify)
        graph.add_node("plan", self.plan)
        graph.add_node("approve", self.approve)
        graph.add_node("research", self.research)
        graph.add_node("gap_check", self.gap_check)
        graph.add_node("synthesize", self.synthesize)
        graph.add_node("verify", self.verify)
        graph.add_node("render", self.render)
        graph.add_edge(START, "clarify")
        graph.add_edge("clarify", "plan")
        graph.add_edge("plan", "approve")
        graph.add_edge("approve", "research")
        graph.add_edge("research", "gap_check")
        graph.add_conditional_edges("gap_check", self.route, {"research": "research", "synthesize": "synthesize"})
        graph.add_edge("synthesize", "verify")
        graph.add_edge("verify", "render")
        graph.add_edge("render", END)
        self.graph = graph.compile(checkpointer=checkpointer)

    async def gate(self, run_id):
        run = self.store.run(run_id)
        if run["status"] in ("cancel_requested", "cancelled"):
            raise RunCancelled()
        if run["status"] in ("pause_requested", "paused"):
            raise RunPaused()

    async def clarify(self, state):
        run = self.store.run(state["run_id"])
        await self.gate(run["id"])
        request = dict(state.get("request") or {})
        question = request.get("question") or run["question"]
        subjects, dimensions = infer_scope(question, request)
        request["subjects"] = subjects
        request["dimensions"] = dimensions
        if not needs_clarify(question, request):
            return {"request": request}
        prompt = "还缺少研究对象或比较维度。请各写至少一项；时间与地区会写成计划上的默认范围，不再追问。"
        current = dict(run.get("plan") or {})
        current.update({"waiting": "clarify", "clarify_question": prompt, "request": request})
        self.store.update(run["id"], plan=current)
        self.store.event(run["id"], "run.waiting_input", {"kind": "clarify", "question": prompt})
        decision = interrupt({"kind": "clarify", "question": prompt, "missing": [name for name, values in (("subjects", subjects), ("dimensions", dimensions)) if not values]})
        merged = {
            **request,
            "subjects": [item for item in (decision.get("subjects") or subjects) if str(item).strip()],
            "dimensions": [item for item in (decision.get("dimensions") or dimensions) if str(item).strip()],
        }
        if not merged["subjects"] or not merged["dimensions"]:
            raise ValueError("澄清后仍缺少研究对象或比较维度")
        return {"request": merged}

    async def plan(self, state):
        run = self.store.run(state["run_id"])
        await self.gate(run["id"])
        request = state.get("request") or {}
        self.store.event(run["id"], "plan.started", {})
        plan = await self.provider.plan(run, request)
        plan = apply_plan_defaults(plan, request.get("question") or run["question"])
        self.store.update(run["id"], plan=plan)
        self.store.event(run["id"], "plan.proposed", {"subjects": plan["subjects"], "dimensions": plan["dimensions"], "as_of": plan.get("as_of"), "regions": plan.get("regions")})
        return {"plan": plan, "round": 0}

    async def approve(self, state):
        decision = interrupt({"kind": "approve_plan", "plan": state["plan"]})
        return {"plan": decision["plan"]}

    async def research(self, state):
        run = self.store.run(state["run_id"])
        await self.gate(run["id"])
        plan = state["plan"]
        semaphore = asyncio.Semaphore(max(1, self.settings.max_researchers))
        parent = self.store.run(run["parent_run_id"]) if run["parent_run_id"] else None

        async def subject_task(subject, dimensions):
            async with semaphore:
                if state.get("round", 0) == 0 and parent and parent["artifact"] and parent["mode"] == run["mode"] and parent["plan"]["dimensions"] == plan["dimensions"] and subject in parent["plan"]["subjects"]:
                    # Reuse only parent evidence, retaining original fetch time. Live sources expire after a day.
                    from datetime import datetime, timezone, timedelta
                    old = [e for e in self.store.evidence(parent["id"]) if e["task_key"].startswith(f"research:{subject}:")]
                    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
                    old = [e for e in old if run["mode"] == "demo" or datetime.fromisoformat(e["fetched_at"]) >= cutoff]
                    if old:
                        ids = [self.store.add_evidence(run["project_id"], run["id"], f"research:{subject}:0", e) for e in old]
                        self.store.event(run["id"], "task.reused", {"subject": subject, "reason": "父任务相同维度的有效证据"})
                        return {"subject": subject, "evidence_ids": ids, "error": None, "dimensions": dimensions}
                try:
                    return await self.provider.research(run, plan, subject, state.get("round", 0), lambda: self.gate(run["id"]), dimensions)
                except (RunPaused, RunCancelled):
                    raise
                except Exception as exc:
                    log.exception("Researcher failed run=%s subject=%s", run["id"], subject)
                    self.store.event(run["id"], "task.failed", {"subject": subject, "error": type(exc).__name__})
                    return {"subject": subject, "evidence_ids": [], "error": "研究工具失败，请查看后台日志与调用轨迹", "dimensions": dimensions}

        targets = research_targets(plan, state.get("gaps"))
        # Cancel sibling coroutines before resuming a failed node to prevent stale writes.
        tasks = [asyncio.create_task(subject_task(subject, dimensions)) for subject, dimensions in targets]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return {"results": state.get("results", []) + results, "gaps": []}

    async def gap_check(self, state):
        await self.gate(state["run_id"])
        evidence = self.store.evidence(state["run_id"])
        coverage = coverage_matrix(state["plan"], evidence)
        gaps = coverage["gaps"]
        current_round = state.get("round", 0)
        model_left = self.store.usage_count(state["run_id"], "model") < self.settings.max_model_calls - self.settings.report_reserved_calls
        search_left = self.store.usage_count(state["run_id"], "search") < state["plan"]["max_search_calls"]
        more = bool(gaps and model_left and search_left and current_round < state["plan"]["max_gap_rounds"])
        covered_subjects = {item["subject"] for item in coverage["cells"] if item["covered"]}
        self.store.event(state["run_id"], "coverage.checked", {
            "cells_covered": coverage["covered"],
            "cells_total": coverage["total"],
            "subjects_with_evidence": len(covered_subjects),
            "subjects_total": len(state["plan"]["subjects"]),
            "gaps": gaps,
            "will_retry": more,
        })
        return {"gaps": gaps if more else [], "round": current_round + 1, "coverage": coverage}

    def route(self, state):
        return "research" if state.get("gaps") else "synthesize"

    async def synthesize(self, state):
        run = self.store.run(state["run_id"])
        await self.gate(run["id"])
        self.store.event(run["id"], "report.started", {})
        bundle = await self.provider.synthesize(run, state["plan"], self.store.evidence(run["id"]), lambda: self.gate(run["id"]))
        return {"bundle": bundle}

    async def verify(self, state):
        run = self.store.run(state["run_id"])
        await self.gate(run["id"])
        self.store.event(run["id"], "verify.started", {"mode": run["mode"]})
        bundle = await self.provider.verify(run, state["plan"], state["bundle"], self.store.evidence(run["id"]), lambda: self.gate(run["id"]))
        self.store.event(run["id"], "verify.finished", {"mode": run["mode"]})
        return {"bundle": bundle}

    async def render(self, state):
        run = self.store.run(state["run_id"])
        await self.gate(run["id"])
        evidence = self.store.evidence(run["id"])
        claims = validate_claims(state["plan"], state["bundle"], evidence)
        unknown = sum(c["kind"] == "unknown" for c in claims)
        coverage = coverage_matrix(state["plan"], evidence)
        supported = sum(c.get("verification") in {"fully", "partial", "reference_checked"} for c in claims)
        artifact = {"title": run["question"], "summary": state["bundle"].get("summary", ""), "claims": claims, "mode": run["mode"], "verification_note": verification_note(run["mode"]), "coverage": coverage, "metrics": {"claims": len(claims), "evidence": len(evidence), "unknown": unknown, "with_references": sum(bool(c["evidence_ids"]) for c in claims), "supported": supported, "cells_covered": coverage["covered"], "cells_total": coverage["total"]}}
        self.store.update(run["id"], artifact=artifact, status="partial" if unknown else "completed", error=None)
        return {}

    def schedule(self, run_id, input):
        task = self.tasks.get(run_id)
        if task and not task.done():
            raise ValueError("任务仍在执行，请等待状态更新")
        self.tasks[run_id] = asyncio.create_task(self.execute(run_id, input))

    async def execute(self, run_id, input):
        try:
            async with self.run_slots:
                await self.gate(run_id)
                async with asyncio.timeout(self.settings.run_timeout_seconds):
                    output = await self.graph.ainvoke(input, {"configurable": {"thread_id": run_id}, "recursion_limit": 30})
                if output.get("__interrupt__"):
                    await self.gate(run_id)
                    self.store.update(run_id, status="waiting_input")
        except RunPaused:
            self.store.update(run_id, status="paused")
        except RunCancelled:
            self.store.update(run_id, status="cancelled")
        except asyncio.CancelledError:
            if self.store.run(run_id)["status"] not in ("completed", "partial", "cancelled", "waiting_input"):
                self.store.update(run_id, status="paused")
            raise
        except Exception as exc:
            log.exception("Run failed: %s", run_id)
            from .providers import BudgetExceeded
            if isinstance(exc, (BudgetExceeded, ValueError)):
                reason = str(exc)[:500]
            elif isinstance(exc, TimeoutError):
                reason = "研究执行超时，已保存的证据保留，可重试继续。"
            else:
                reason = f"{type(exc).__name__}：执行失败，请查看本地后台日志；已保存的证据保留。"
            self.store.update(run_id, status="failed", error=reason)

    async def shutdown(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def resume_input(self, run_id):
        snapshot = await self.graph.aget_state({"configurable": {"thread_id": run_id}})
        if not snapshot.values:
            run = self.store.run(run_id)
            return {"run_id": run_id, "request": run["plan"].get("request", {"question": run["question"]})}
        if any(getattr(task, "interrupts", ()) for task in snapshot.tasks):
            run = self.store.run(run_id)
            plan = run.get("plan") or {}
            if plan.get("waiting") == "clarify":
                request = plan.get("request") or {}
                return Command(resume={"subjects": request.get("subjects") or [], "dimensions": request.get("dimensions") or []})
            return Command(resume={"plan": plan})
        return None
