"""Infer research scope and write visible plan defaults. Do not invent subjects."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

KNOWN_DIMENSIONS = (
    "产品形态",
    "研究流程",
    "交付方式",
    "定价",
    "部署方式",
    "目标用户",
    "产品定位",
    "核心能力",
    "用户场景",
)
KNOWN_SUBJECTS = ("ChatGPT", "Gemini", "Claude", "Cursor", "Copilot", "Atlas", "Beacon")
KNOWN_REGIONS = ("中国", "全球", "美国", "欧洲", "日本")
LEAD = re.compile(r"(?:比较|对比|调研|分析)\s*([^。；\n]{2,80})")


def truncate(text: str, limit: int = 80) -> str:
    value = " ".join((text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def clean_items(values, limit: int) -> list[str]:
    out = []
    for item in values or []:
        text = str(item).strip()
        if text and text not in out and len(text) <= 120:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def infer_scope(question: str, request: dict | None = None) -> tuple[list[str], list[str]]:
    request = request or {}
    subjects = clean_items(request.get("subjects"), 6)
    dimensions = clean_items(request.get("dimensions"), 8)
    text = question or ""
    if not subjects:
        match = LEAD.search(text)
        if match:
            chunk = re.split(r"的", match.group(1), 1)[0]
            parts = [part.strip() for part in re.split(r"[、，,/]|与|和", chunk) if part.strip()]
            subjects = clean_items(
                [part for part in parts if part not in KNOWN_DIMENSIONS and 1 < len(part) <= 40],
                6,
            )
            if len(subjects) < 2:
                subjects = []
        if not subjects:
            lowered = text.lower()
            subjects = clean_items([name for name in KNOWN_SUBJECTS if name.lower() in lowered], 6)
    if not dimensions:
        dimensions = clean_items([item for item in KNOWN_DIMENSIONS if item in text], 8)
    return subjects, dimensions


def needs_clarify(question: str, request: dict | None = None) -> bool:
    subjects, dimensions = infer_scope(question, request)
    return not subjects or not dimensions


def apply_plan_defaults(plan: dict, question: str = "") -> dict:
    next_plan = dict(plan)
    if not str(next_plan.get("as_of") or "").strip():
        next_plan["as_of"] = date.today().isoformat()
    regions = clean_items(next_plan.get("regions"), 8)
    if not regions:
        regions = clean_items([item for item in KNOWN_REGIONS if item in (question or "")], 8) or ["未限定"]
    next_plan["regions"] = regions
    return next_plan


def parse_iso_date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
