"""Map persisted SSE events + run status onto the real graph stages."""

from __future__ import annotations

from datetime import datetime, timezone

# Real graph: clarify → plan → approve → research → gap_check → (research) → synthesize → verify → render.
STAGES = (
    {"id": "clarify", "label": "澄清范围", "node": "clarify"},
    {"id": "plan", "label": "规划", "node": "plan"},
    {"id": "approve", "label": "计划确认", "node": "approve"},
    {"id": "research", "label": "检索/阅读", "node": "research"},
    {"id": "gap_check", "label": "缺口与冲突检查", "node": "gap_check"},
    {"id": "supplement", "label": "补充研究", "node": "research"},
    {"id": "synthesize", "label": "综合", "node": "synthesize"},
    {"id": "verify", "label": "原文核验", "node": "verify"},
    {"id": "render", "label": "校验", "node": "render"},
    {"id": "report", "label": "生成报告", "node": "render"},
)

STAGE_IDS = [item["id"] for item in STAGES]


def _payload(event: dict) -> dict:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def _has(events: list[dict], *types: str) -> bool:
    wanted = set(types)
    return any(event.get("type") in wanted for event in events)


def _last(events: list[dict], *types: str) -> dict | None:
    wanted = set(types)
    for event in reversed(events):
        if event.get("type") in wanted:
            return event
    return None


def activity_line(events: list[dict], *, evidence_count: int = 0, status: str = "", run: dict | None = None) -> str:
    if status == "waiting_input":
        waiting = ((run or {}).get("plan") or {}).get("waiting")
        if waiting == "clarify":
            return "还缺少研究对象或比较维度，请先补全"
        return "等待你确认研究计划"
    if status == "paused":
        return "研究已暂停，可从当前步骤继续"
    if status == "failed":
        return "研究执行失败，已保留已有证据与记录"
    event = events[-1] if events else None
    if not event:
        return "任务已进入后台"
    kind = event.get("type")
    payload = _payload(event)
    subject = str(payload.get("subject") or "")
    tool = str(payload.get("tool") or "")
    query = str(payload.get("query") or "")
    if kind == "tool.started" and tool in {"search_web", "search_project"}:
        return f"正在搜索 {query or subject or '来源'}"
    if kind == "tool.started" and tool == "read_source":
        return f"正在阅读来源 {payload.get('url') or ''}".strip()
    if kind == "tool.finished" and payload.get("failed"):
        return f"工具 {tool or '调用'} 失败，将尝试其他来源"
    if kind == "tool.finished" and tool == "read_source":
        return f"已读取来源，当前共 {evidence_count} 条原文"
    if kind == "evidence.created":
        return f"已保存原文「{payload.get('title') or ''}」，共 {evidence_count} 条"
    if kind == "task.started":
        round_index = int(payload.get("round") or 0)
        if round_index:
            return f"第 {round_index + 1} 轮补充：正在调查 {subject}"
        return f"正在调查 {subject}"
    if kind == "task.finished":
        return f"已完成 {subject} 的检索，保存 {payload.get('count') or 0} 条证据"
    if kind == "task.reused":
        return f"复用 {subject} 的已有证据"
    if kind == "coverage.checked":
        if payload.get("will_retry"):
            return f"发现缺口，将进行第 {int(payload.get('round') or 1) + 1} 轮补充"
        conflicts = payload.get("conflicts") or []
        return f"覆盖检查完成，冲突 {len(conflicts)} 处"
    if kind == "verify.started" or (kind == "stage.started" and payload.get("stage") == "verify"):
        return "正在对照原文核验引用"
    if kind == "report.started" or kind == "stage.started" and payload.get("stage") == "synthesize":
        return "正在综合证据并起草结论"
    if kind == "stage.started" and payload.get("stage") == "render":
        return "正在校验引用结构"
    if kind in {"run.completed", "run.partial"}:
        return "研究报告已生成"
    if kind == "plan.started":
        return "正在拆解研究目标"
    if kind == "plan.proposed":
        return "研究计划已生成，等待确认"
    if kind == "model.started":
        return f"正在调用模型（{payload.get('role') or payload.get('operation') or '研究'}）"
    if kind == "model.finished":
        return "模型调用完成"
    if kind == "budget.reached":
        return "研究预算已达上限，转入报告阶段"
    return {
        "run.created": "已创建研究任务",
        "run.running": "研究已启动",
        "run.waiting_input": "等待你确认研究计划",
        "task.failed": f"{subject} 调查失败" if subject else "子任务失败",
        "run.failed": "任务执行失败",
        "run.paused": "已保存进度",
    }.get(kind, "")


def _parse_stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def elapsed_seconds(run: dict, events: list[dict], now: datetime | None = None) -> int:
    start = _parse_stamp(run.get("created_at"))
    if not start:
        return 0
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if run.get("status") in {"completed", "partial", "failed", "cancelled"}:
        end = _parse_stamp(run.get("updated_at")) or (_parse_stamp((events[-1] or {}).get("created_at")) if events else None)
    else:
        end = now or datetime.now(timezone.utc)
    if end and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if not end:
        return 0
    return max(0, int((end - start).total_seconds()))


def map_progress(events: list[dict], run: dict) -> dict:
    status = run.get("status") or ""
    failed = status == "failed"
    cancelled = status in {"cancelled", "cancel_requested"}
    terminal_ok = status in {"completed", "partial"}
    waiting = status == "waiting_input"
    paused = status in {"paused", "pause_requested"}

    plan_done = _has(events, "plan.proposed")
    research_started = _has(events, "task.started", "task.reused", "tool.started")
    research_done = _has(events, "coverage.checked") or _has(events, "report.started")
    gap_done = _has(events, "coverage.checked")
    last_coverage = _last(events, "coverage.checked")
    will_retry = bool(_payload(last_coverage).get("will_retry")) if last_coverage else False
    supplement_events = [event for event in events if event.get("type") in {"task.started", "task.reused", "tool.started"} and int(_payload(event).get("round") or 0) > 0]
    supplement_used = bool(will_retry or supplement_events or any(int(_payload(event).get("round") or 0) > 0 for event in events if event.get("type") == "coverage.checked"))
    synthesize_started = _has(events, "report.started") or any(_payload(event).get("stage") == "synthesize" for event in events if event.get("type") == "stage.started")
    verify_started = _has(events, "verify.started", "verify.finished") or any(_payload(event).get("stage") == "verify" for event in events if event.get("type") == "stage.started")
    render_started = any(_payload(event).get("stage") == "render" for event in events if event.get("type") == "stage.started") or _has(events, "claims.validated")
    report_done = terminal_ok or _has(events, "run.completed", "run.partial")
    clarifying = waiting and ((run.get("plan") or {}).get("waiting") == "clarify" or any(_payload(event).get("kind") == "clarify" for event in events if event.get("type") == "run.waiting_input"))

    def state_for(stage_id: str) -> str:
        if cancelled and stage_id not in {"plan", "clarify"} and not plan_done:
            return "pending"
        if stage_id == "clarify":
            if plan_done or research_started or report_done:
                return "skipped" if not clarifying and not any(_payload(event).get("kind") == "clarify" for event in events) else "done"
            if clarifying:
                return "waiting"
            return "skipped"
        if stage_id == "plan":
            if plan_done:
                return "done"
            if failed and _has(events, "plan.started"):
                return "failed"
            if _has(events, "plan.started") or status == "planning":
                return "current"
            return "pending"
        if stage_id == "approve":
            if research_started or synthesize_started or report_done:
                return "done"
            if waiting and not clarifying:
                return "waiting"
            if plan_done and status in {"running", "planning"}:
                return "current"
            if failed and plan_done and not research_started:
                return "failed"
            return "pending"
        if stage_id == "research":
            if gap_done or synthesize_started or report_done:
                return "done"
            if research_started:
                return "failed" if failed else "current"
            return "pending"
        if stage_id == "gap_check":
            if synthesize_started or report_done or (gap_done and not will_retry):
                return "done"
            if gap_done and will_retry:
                return "done"
            if research_done or gap_done:
                return "failed" if failed else "current"
            return "pending"
        if stage_id == "supplement":
            if not supplement_used and not will_retry:
                return "skipped"
            if synthesize_started or report_done:
                return "done"
            if supplement_events or will_retry:
                return "failed" if failed else "current"
            return "pending"
        if stage_id == "synthesize":
            if verify_started or render_started or report_done:
                return "done"
            if synthesize_started:
                return "failed" if failed else "current"
            return "pending"
        if stage_id == "verify":
            if report_done or render_started:
                return "done"
            if verify_started:
                return "failed" if failed else "current"
            if synthesize_started:
                return "pending"
            return "skipped" if not synthesize_started and not report_done else "pending"
        if stage_id == "render":
            if report_done:
                return "done"
            if render_started:
                return "failed" if failed else "current"
            return "pending"
        if stage_id == "report":
            if report_done:
                return "done"
            if render_started and status == "running":
                return "current"
            if failed and synthesize_started:
                return "failed"
            return "pending"
        return "pending"

    stages = []
    current = None
    for item in STAGES:
        state = state_for(item["id"])
        if state == "current" or state == "waiting":
            current = item["id"]
        stages.append({**item, "state": state})
    if not current:
        if clarifying:
            current = "clarify"
        elif waiting:
            current = "approve"
        elif terminal_ok:
            current = "report"
        elif failed:
            current = next((item["id"] for item in reversed(stages) if item["state"] == "failed"), "plan")

    evidence_count = 0
    for event in events:
        if event.get("type") == "evidence.created":
            evidence_count += 1
    if isinstance(run.get("artifact"), dict):
        evidence_count = max(evidence_count, int((run["artifact"].get("metrics") or {}).get("evidence") or 0))

    return {
        "stages": stages,
        "current": current,
        "activity": activity_line(events, evidence_count=evidence_count, status=status, run=run),
        "elapsed_seconds": elapsed_seconds(run, events),
        "waiting": waiting,
        "paused": paused,
        "failed": failed,
        "cancelled": cancelled,
        "evidence_count": evidence_count,
        "round": int(_payload(_last(events, "task.started", "coverage.checked") or {}).get("round") or 0),
    }
