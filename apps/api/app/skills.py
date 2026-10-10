"""Versioned markdown skills. The planner sees a catalog first, then the chosen body."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import ROOT

SKILLS_DIR = ROOT / "skills"
KNOWN_TOOLS = frozenset({"search_web", "read_source", "search_project", "cite_project"})


@dataclass(frozen=True)
class Skill:
    name: str
    version: str
    title: str
    when_to_use: str
    required_dimensions: list[str]
    allowed_tools: list[str]
    output_schema: dict
    body: str

    def catalog_entry(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "title": self.title,
            "when_to_use": self.when_to_use,
            "required_dimensions": list(self.required_dimensions),
        }


def parse_frontmatter(text: str) -> tuple[dict, str]:
    raw = text.lstrip("\ufeff")
    if not raw.startswith("---"):
        raise ValueError("skill 缺少 YAML frontmatter")
    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise ValueError("skill frontmatter 未闭合")
    meta: dict = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith("{") or value.startswith("["):
            meta[key] = json.loads(value)
        elif key in {"required_dimensions", "allowed_tools"}:
            meta[key] = [item.strip() for item in value.split(",") if item.strip()]
        else:
            meta[key] = value.strip().strip('"')
    return meta, parts[2].strip()


def _skill_from_path(path: Path) -> Skill:
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    name = str(meta.get("name") or path.stem).strip()
    tools = [item for item in (meta.get("allowed_tools") or []) if item in KNOWN_TOOLS]
    if not tools:
        raise ValueError(f"{name} 未声明任何已有工具")
    extra = set(meta.get("allowed_tools") or []) - KNOWN_TOOLS
    if extra:
        raise ValueError(f"{name} 声明了未知工具：{sorted(extra)}")
    schema = meta.get("output_schema") or {}
    if not isinstance(schema, dict):
        raise ValueError(f"{name} 的 output_schema 必须是对象")
    if not body:
        raise ValueError(f"{name} 正文为空")
    return Skill(
        name=name,
        version=str(meta.get("version") or "1"),
        title=str(meta.get("title") or name),
        when_to_use=str(meta.get("when_to_use") or ""),
        required_dimensions=list(meta.get("required_dimensions") or []),
        allowed_tools=tools,
        output_schema=schema,
        body=body,
    )


@lru_cache(maxsize=1)
def load_skills() -> dict[str, Skill]:
    if not SKILLS_DIR.is_dir():
        raise FileNotFoundError(f"未找到 skills 目录：{SKILLS_DIR}")
    skills = {}
    for path in sorted(SKILLS_DIR.glob("*.md")):
        skill = _skill_from_path(path)
        skills[skill.name] = skill
    if not skills:
        raise FileNotFoundError("skills/ 下没有可用的 markdown 技能")
    return skills


def skill_catalog() -> list[dict]:
    return [skill.catalog_entry() for skill in load_skills().values()]


def load_skill(name: str) -> Skill:
    skills = load_skills()
    if name not in skills:
        raise KeyError(name)
    return skills[name]


def choose_skill(question: str, subjects: list[str] | None = None, dimensions: list[str] | None = None, catalog: list[dict] | None = None) -> str:
    entries = catalog or skill_catalog()
    names = {item["name"] for item in entries}
    subjects = [item for item in (subjects or []) if str(item).strip()]
    text = f"{question or ''} {' '.join(subjects)}"
    if "competitor_analysis" in names and (len(subjects) >= 2 or any(token in text for token in ("竞品", "对比", "比较", "对照", "competitor"))):
        if any(token in text for token in ("竞品", "对比", "比较", "对照", "competitor")) or len(subjects) >= 2:
            return "competitor_analysis"
    if "industry_landscape" in names and any(token in text for token in ("行业", "格局", "landscape", "产业", "行业观点")):
        return "industry_landscape"
    if "research_report" in names:
        return "research_report"
    return entries[0]["name"]


def parse_skill_choice(content: str, catalog: list[dict] | None = None) -> str | None:
    entries = catalog or skill_catalog()
    names = {item["name"] for item in entries}
    try:
        payload = json.loads(content or "{}")
    except json.JSONDecodeError:
        payload = {}
    name = str(payload.get("skill_name") or payload.get("name") or "").strip()
    return name if name in names else None


def inject_skill(plan: dict, skill: Skill) -> dict:
    next_plan = dict(plan)
    next_plan["skill_name"] = skill.name
    next_plan["skill_version"] = skill.version
    return next_plan


def skill_prompt(skill: Skill) -> str:
    return (
        f"已选择技能 {skill.name} v{skill.version}（{skill.title}）。\n"
        f"{skill.body}\n"
        f"输出 Schema: {json.dumps(skill.output_schema, ensure_ascii=False)}"
    )


def allowed_tools_for(plan: dict) -> list[str]:
    name = (plan or {}).get("skill_name") or "research_report"
    try:
        skill = load_skill(name)
    except KeyError:
        skill = load_skill("research_report")
    return [tool for tool in skill.allowed_tools if tool in KNOWN_TOOLS]
