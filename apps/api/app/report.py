"""Build a long-form report artifact from claims, plan, and evidence."""

from __future__ import annotations

from .export import report_fields, slug, sources_from_evidence
from .research_mode import find_subquestion, flatten_subquestions


def _cite(ids: list[str]) -> str:
    return "".join(f"[[{item}]]" for item in ids[:3])


def sections_from_general(plan: dict, claims: list[dict]) -> tuple[list[dict], list[dict]]:
    by_id = {}
    for claim in claims:
        ident = claim.get("subquestion_id") or claim.get("subject")
        by_id[ident] = claim
    outline = []
    sections = []
    questions = flatten_subquestions(plan)
    children = {}
    for item in questions:
        children.setdefault(item.get("parent_id"), []).append(item)
    for item in children.get(None, []):
        outline.append({"id": item["id"], "title": item["question"]})
        parts = []
        ids = []
        own = by_id.get(item["id"])
        if own:
            parts.append(f"{own.get('text') or ''} {_cite(own.get('evidence_ids') or [])}".strip())
            ids.extend(own.get("evidence_ids") or [])
        for child in children.get(item["id"], []):
            nested = by_id.get(child["id"])
            if not nested:
                continue
            parts.append(f"{child['question']} {nested.get('text') or ''} {_cite(nested.get('evidence_ids') or [])}".strip())
            ids.extend(nested.get("evidence_ids") or [])
        sections.append({"id": item["id"], "title": item["question"], "body": "\n\n".join(parts) or "待确认：该子问题没有足够原文。", "evidence_ids": list(dict.fromkeys(ids))})
    return outline, sections


def sections_from_grid(plan: dict, claims: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    subjects = list(plan.get("subjects") or [])
    dimensions = list(plan.get("dimensions") or [])
    outline = []
    sections = []
    for subject in subjects:
        ident = slug(subject)
        outline.append({"id": ident, "title": subject})
        parts = []
        ids = []
        for claim in claims:
            if claim.get("subject") != subject:
                continue
            parts.append(f"{claim.get('dimension')}：{claim.get('text') or ''} {_cite(claim.get('evidence_ids') or [])}".strip())
            ids.extend(claim.get("evidence_ids") or [])
        sections.append({"id": ident, "title": subject, "body": "\n\n".join(parts), "evidence_ids": list(dict.fromkeys(ids))})
    rows = []
    for dimension in dimensions:
        row = [dimension]
        for subject in subjects:
            claim = next((item for item in claims if item.get("subject") == subject and item.get("dimension") == dimension), None)
            row.append((claim or {}).get("text") or "未确认")
        rows.append(row)
    tables = [{"id": "comparison", "title": "对照表", "headers": ["维度", *subjects], "rows": rows}] if subjects and dimensions else []
    return outline, sections, tables


def default_open_questions(plan: dict, claims: list[dict]) -> list[str]:
    missing = [item for item in claims if item.get("kind") == "unknown"]
    if (plan or {}).get("mode") == "general":
        questions = []
        for claim in missing:
            item = find_subquestion(plan, claim.get("subquestion_id") or claim.get("subject"))
            questions.append((item or {}).get("question") or claim.get("dimension") or "仍有子问题缺少原文")
        return questions[:8] or ["仍需更多一手来源核对记忆评测与长期写入冲突。"]
    return [f"{item.get('subject')} · {item.get('dimension')} 仍待确认" for item in missing][:8]


def attach_report(artifact: dict, plan: dict, evidence: list[dict], bundle: dict | None = None) -> dict:
    next_artifact = dict(artifact)
    bundle = bundle or {}
    claims = list(next_artifact.get("claims") or [])
    if bundle.get("outline"):
        next_artifact["outline"] = bundle["outline"]
    if bundle.get("sections"):
        next_artifact["sections"] = bundle["sections"]
    if bundle.get("open_questions"):
        next_artifact["open_questions"] = bundle["open_questions"]
    if bundle.get("charts"):
        next_artifact["charts"] = bundle["charts"]
    if bundle.get("tables"):
        next_artifact["tables"] = bundle["tables"]
    if not next_artifact.get("sections"):
        if (plan or {}).get("mode") == "general":
            outline, sections = sections_from_general(plan, claims)
            tables = []
        else:
            outline, sections, tables = sections_from_grid(plan, claims)
        next_artifact["outline"] = next_artifact.get("outline") or outline
        next_artifact["sections"] = sections
        if not next_artifact.get("tables"):
            next_artifact["tables"] = tables
    if not next_artifact.get("open_questions"):
        next_artifact["open_questions"] = default_open_questions(plan, claims)
    next_artifact["sources"] = sources_from_evidence(evidence)
    fields = report_fields(next_artifact)
    next_artifact.update({key: fields[key] for key in ("outline", "sections", "open_questions", "sources") if not next_artifact.get(key)})
    return next_artifact
