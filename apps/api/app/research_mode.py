"""Grid vs general research mode: infer, normalize sub-questions, per-question coverage."""

from __future__ import annotations

import re

from .scope import clean_items, infer_scope, truncate

COMPARISON_HINTS = ("竞品", "对比", "比较", "对照", "vs", "versus", "competitor")
GENERAL_HINTS = ("如何工作", "怎么运作", "原理", "机制", "走向", "趋势", "为什么", "怎样", "how do", "how does", "where is the field")


def flatten_subquestions(plan_or_items) -> list[dict]:
    if isinstance(plan_or_items, dict):
        items = list(plan_or_items.get("subquestions") or [])
    else:
        items = list(plan_or_items or [])
    out = []
    seen = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        question = " ".join(str(item.get("question") or "").split())
        if not question:
            continue
        ident = str(item.get("id") or f"sq_{index + 1}").strip() or f"sq_{index + 1}"
        if ident in seen:
            ident = f"{ident}_{index + 1}"
        seen.add(ident)
        parent = item.get("parent_id")
        parent_id = str(parent).strip() if parent else None
        if parent_id == ident:
            parent_id = None
        out.append({"id": ident[:40], "question": question[:400], "parent_id": parent_id})
    valid = {item["id"] for item in out}
    for item in out:
        if item["parent_id"] not in valid:
            item["parent_id"] = None
    return out[:24]


def subquestion_ids(plan: dict) -> list[str]:
    return [item["id"] for item in flatten_subquestions(plan)]


def find_subquestion(plan: dict, ident: str | None) -> dict | None:
    if not ident:
        return None
    for item in flatten_subquestions(plan):
        if item["id"] == ident or item["question"] == ident:
            return item
    return None


def looks_like_comparison(question: str, subjects: list[str] | None = None) -> bool:
    text = question or ""
    lowered = text.lower()
    if any(hint in text or hint in lowered for hint in COMPARISON_HINTS):
        return True
    return len([item for item in (subjects or []) if item]) >= 2


def infer_mode(question: str, request: dict | None = None, skill_name: str | None = None) -> str:
    request = request or {}
    requested = str(request.get("mode") or "").strip()
    if requested in {"grid", "general"}:
        return requested
    subjects, dimensions = infer_scope(question, request)
    if skill_name == "competitor_analysis":
        return "grid"
    if looks_like_comparison(question, subjects):
        return "grid"
    if subjects and dimensions and len(subjects) >= 2:
        return "grid"
    if skill_name == "industry_landscape":
        return "general"
    text = (question or "").lower()
    if any(hint in (question or "") or hint in text for hint in GENERAL_HINTS):
        return "general"
    if not subjects or not dimensions:
        return "general"
    return "grid"


def needs_clarify(question: str, request: dict | None = None) -> bool:
    request = request or {}
    if infer_mode(question, request) == "general":
        return False
    subjects, dimensions = infer_scope(question, request)
    return not subjects or not dimensions


def topic_label(question: str, limit: int = 40) -> str:
    text = " ".join((question or "").split())
    text = re.sub(r"^(调研|分析|研究|梳理|请|帮我)\s*", "", text)
    return truncate(text, limit) or "研究主题"


def default_subquestions(question: str) -> list[dict]:
    text = question or ""
    if any(token in text for token in ("记忆", "memory")):
        return [
            {"id": "sq_arch", "question": "智能体记忆系统的常见架构有哪些？", "parent_id": None},
            {"id": "sq_short", "question": "短期与工作记忆通常如何实现？", "parent_id": "sq_arch"},
            {"id": "sq_long", "question": "长期记忆如何写入与检索？", "parent_id": "sq_arch"},
            {"id": "sq_practice", "question": "当前主流产品与研究如何做记忆？", "parent_id": None},
            {"id": "sq_future", "question": "未解问题与领域走向是什么？", "parent_id": None},
        ]
    topic = topic_label(text, 24)
    return [
        {"id": "sq_what", "question": f"{topic}的核心概念与定义是什么？", "parent_id": None},
        {"id": "sq_how", "question": f"现有做法与代表工作如何处理{topic}？", "parent_id": None},
        {"id": "sq_evidence", "question": "公开资料里有哪些可核对的关键证据？", "parent_id": "sq_how"},
        {"id": "sq_open", "question": "还有哪些未解问题，领域可能往何处去？", "parent_id": None},
    ]


def grid_to_subquestions(subjects: list[str], dimensions: list[str]) -> list[dict]:
    items = []
    for subject in subjects or []:
        parent = f"sq_{len(items) + 1}"
        items.append({"id": parent, "question": f"{subject} 的整体情况如何？", "parent_id": None})
        for dimension in dimensions or []:
            items.append({"id": f"sq_{len(items) + 1}", "question": f"{subject} 的{dimension}是什么？", "parent_id": parent})
        if len(items) >= 20:
            break
    return flatten_subquestions(items)


def demo_followup_query(subject: str, plan: dict) -> str:
    item = find_subquestion(plan, subject)
    question = (item or {}).get("question") or subject
    mapping = {
        "sq_arch": "记忆层级如何与上下文窗口配合？",
        "sq_short": "工作记忆溢出时有哪些压缩或遗忘策略？",
        "sq_long": "长期记忆检索常用的向量与符号混合方案有哪些公开描述？",
        "sq_practice": "ChatGPT、Claude 或开源智能体框架如何描述记忆模块？",
        "sq_future": "记忆评测基准与开放问题有哪些近期综述？",
        "sq_what": f"{question} 还有哪些权威定义需要核对？",
        "sq_how": f"{question} 有哪些被引用最多的实现？",
        "sq_evidence": "上述做法是否有可引用的原文段落？",
        "sq_open": "近期论文对未解问题怎么表述？",
    }
    if subject in mapping:
        return mapping[subject]
    return f"关于「{question}」还缺哪些一手来源？"


def coverage_subquestions(plan: dict, evidence: list[dict]) -> dict:
    from .store import subject_from_task_key

    questions = flatten_subquestions(plan)
    cells = []
    for item in questions:
        ids = []
        for row in evidence:
            owner = subject_from_task_key(row.get("task_key") or "")
            dims = row.get("dimensions") or []
            if isinstance(dims, str):
                dims = [dims]
            if owner == item["id"] or item["id"] in dims or item["question"] in dims:
                if row["id"] not in ids:
                    ids.append(row["id"])
        cells.append({
            "subject": item["id"],
            "dimension": item["question"],
            "subquestion_id": item["id"],
            "question": item["question"],
            "parent_id": item.get("parent_id"),
            "evidence_ids": ids,
            "covered": bool(ids),
        })
    gaps = [{"subject": cell["subject"], "dimension": cell["dimension"], "subquestion_id": cell["subquestion_id"]} for cell in cells if not cell["covered"]]
    return {
        "mode": "general",
        "subjects": [item["id"] for item in questions],
        "dimensions": [item["question"] for item in questions],
        "subquestions": questions,
        "cells": cells,
        "covered": sum(1 for cell in cells if cell["covered"]),
        "total": len(cells),
        "gaps": gaps,
    }


def claim_pairs(plan: dict) -> list[tuple[str, str]]:
    if (plan or {}).get("mode") == "general":
        return [(item["id"], item["question"]) for item in flatten_subquestions(plan)]
    return [(subject, dimension) for subject in plan.get("subjects") or [] for dimension in plan.get("dimensions") or []]
