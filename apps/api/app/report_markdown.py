"""Build the primary Markdown deliverable from a run artifact and evidence."""

from __future__ import annotations

import re


def page_from_locator(locator: str | None) -> int | None:
    match = re.search(r"page:(\d+)", locator or "")
    return int(match.group(1)) if match else None


def _escape_cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _cite_label(index: int) -> str:
    return f"[{index}]"


def number_evidence(evidence: list[dict]) -> list[dict]:
    numbered = []
    for index, item in enumerate(evidence, start=1):
        numbered.append({**item, "n": index})
    return numbered


def reference_line(item: dict) -> str:
    n = item.get("n")
    title = item.get("title") or "未命名来源"
    url = item.get("url") or ""
    page = page_from_locator(item.get("locator"))
    source = item.get("source_type")
    freshness = item.get("freshness_label") or item.get("freshness") or ""
    extras = []
    if source == "document" or (url or "").startswith("project://"):
        extras.append("项目资料")
        if page:
            extras.append(f"第 {page} 页")
    elif source == "demo":
        extras.append("模拟来源")
    if freshness:
        extras.append(str(freshness))
    suffix = f"（{' · '.join(extras)}）" if extras else ""
    if url and not url.startswith("project://"):
        return f"{_cite_label(n)} [{title}]({url}){suffix}"
    if page:
        return f"{_cite_label(n)} {title}{suffix}"
    return f"{_cite_label(n)} {title}{suffix}"


def _claim_cites(claim: dict, by_id: dict[str, dict]) -> str:
    ids = list(claim.get("evidence_ids") or []) + list(claim.get("conflict_evidence_ids") or [])
    marks = []
    seen = set()
    for evidence_id in ids:
        item = by_id.get(evidence_id)
        if not item or item["n"] in seen:
            continue
        seen.add(item["n"])
        marks.append(_cite_label(item["n"]))
    return "".join(marks)


def render_report_markdown(run: dict, evidence: list[dict] | None = None) -> str:
    artifact = run.get("artifact") or {}
    plan = run.get("plan") or {}
    evidence = number_evidence(list(evidence or []))
    by_id = {item["id"]: item for item in evidence if item.get("id")}
    title = artifact.get("title") or run.get("question") or "研究报告"
    summary = (artifact.get("summary") or "").strip()
    note = artifact.get("verification_note") or ""
    mode = run.get("mode") or artifact.get("mode") or ""
    status = run.get("status") or ""
    lines = [f"# {title}", ""]
    if status in {"partial", "failed"}:
        lines.extend(["> 这是一份**部分成果**：研究未完整结束，以下内容仅覆盖已保存的证据与结论。", ""])
    if mode == "demo":
        lines.extend(["> 演示模式：资料均为模拟来源，不能当作真实产品事实。", ""])
    if note:
        lines.extend([f"> {note}", ""])
    if summary:
        lines.extend(["## 摘要", "", summary, ""])
    subjects = list(plan.get("subjects") or [])
    dimensions = list(plan.get("dimensions") or [])
    claims = list(artifact.get("claims") or [])
    if subjects and dimensions and claims:
        lines.extend(["## 对比表", ""])
        header = ["维度", *subjects]
        lines.append("| " + " | ".join(_escape_cell(col) for col in header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")
        for dimension in dimensions:
            row = [_escape_cell(dimension)]
            for subject in subjects:
                claim = next((item for item in claims if item.get("subject") == subject and item.get("dimension") == dimension), None)
                if not claim:
                    row.append("未确认")
                    continue
                text = _escape_cell(claim.get("text") or "未确认")
                row.append(f"{text} {_claim_cites(claim, by_id)}".strip())
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    if claims:
        lines.extend(["## 分节结论", ""])
        for subject in subjects or sorted({item.get("subject") for item in claims}):
            lines.extend([f"### {subject}", ""])
            for claim in [item for item in claims if item.get("subject") == subject]:
                kind = {"fact": "事实", "analysis": "分析", "unknown": "待确认"}.get(claim.get("kind"), claim.get("kind") or "")
                cites = _claim_cites(claim, by_id)
                lines.append(f"**{claim.get('dimension')}**（{kind}）{cites}")
                lines.append("")
                lines.append(claim.get("text") or "未确认")
                lines.append("")
    conflicts = list(artifact.get("conflicts") or [])
    if conflicts:
        lines.extend(["## 来源冲突", ""])
        for conflict in conflicts:
            lines.append(f"### {conflict.get('subject')} · {conflict.get('dimension')}")
            lines.append("")
            for position in conflict.get("positions") or []:
                freshness = position.get("freshness_label") or position.get("freshness") or "未标注"
                cites = "".join(_cite_label(by_id[eid]["n"]) for eid in position.get("evidence_ids") or [] if eid in by_id)
                lines.append(f"- **{position.get('label')}**（{freshness}）{cites}：{position.get('text') or ''}")
            lines.append("")
    metrics = artifact.get("metrics") or {}
    if metrics:
        lines.extend(["## 时效与覆盖", ""])
        lines.append(f"- 原文证据 {metrics.get('evidence', len(evidence))} 条，冲突 {metrics.get('conflicts', len(conflicts))} 处，待确认 {metrics.get('unknown', 0)} 项。")
        lines.append(f"- 较新 {metrics.get('fresh_evidence', 0)} · 过时 {metrics.get('stale_evidence', 0)} · 未标注日期 {metrics.get('undated_evidence', 0)}。")
        lines.append("")
    if evidence:
        lines.extend(["## 参考文献", ""])
        for item in evidence:
            lines.append(reference_line(item))
        lines.append("")
    elif not claims:
        lines.extend(["## 已收集证据", "", "本次尚未形成可引用的原文证据。", ""])
    return "\n".join(lines).strip() + "\n"


def partial_markdown(run: dict, evidence: list[dict] | None = None) -> str:
    if run.get("artifact"):
        return render_report_markdown(run, evidence)
    stub = {
        **run,
        "artifact": {
            "title": run.get("question") or "未完成的研究",
            "summary": "研究未完成，以下仅列出已保存的原文，供继续或重试时核对。",
            "claims": [],
            "conflicts": [],
            "verification_note": "尚未通过引用结构校验。",
            "metrics": {"evidence": len(evidence or []), "claims": 0, "unknown": 0, "conflicts": 0},
        },
    }
    return render_report_markdown(stub, evidence)
