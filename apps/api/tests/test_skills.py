import json
from pathlib import Path
import pytest

pytest_plugins = ("tests.test_workbench",)
from app.config import Settings
from app.providers import Provider
from app.schemas import ResearchPlan
from app.skills import KNOWN_TOOLS, _skill_from_path, choose_skill, inject_skill, load_skill, skill_catalog
from app.store import Store
from tests.test_workbench import new_run


UNIQUE_BODY = {
    "competitor_analysis": "竞品分析技能正文：每个对象同一维度必须能对照。",
    "industry_landscape": "行业格局技能正文：先画价值链再落到玩家与进入壁垒。",
    "research_report": "综合报告技能正文：事实与推断分开，缺口标为未确认。",
}


def test_catalog_omits_body_and_tools_are_existing_subset():
    catalog = skill_catalog()
    names = {item["name"] for item in catalog}
    assert names >= {"competitor_analysis", "industry_landscape", "research_report"}
    catalog_text = json.dumps(catalog, ensure_ascii=False)
    for name, phrase in UNIQUE_BODY.items():
        assert phrase not in catalog_text
        skill = load_skill(name)
        assert phrase in skill.body
        assert set(skill.allowed_tools) <= KNOWN_TOOLS
        assert "name" in skill.catalog_entry() and "when_to_use" in skill.catalog_entry()
        assert "body" not in skill.catalog_entry()
        assert skill.output_schema
    assert "cite_project" not in load_skill("industry_landscape").allowed_tools
    assert "cite_project" in load_skill("competitor_analysis").allowed_tools


def test_choose_skill_from_question():
    catalog = skill_catalog()
    assert choose_skill("比较 ChatGPT 与 Gemini 的产品形态", ["ChatGPT", "Gemini"], ["产品形态"], catalog) == "competitor_analysis"
    assert choose_skill("梳理当前行业观点与进入壁垒", ["生成式 AI"], ["产品定位"], catalog) == "industry_landscape"
    assert choose_skill("整理一份有引用的研究报告", ["主题"], ["交付方式"], catalog) == "research_report"


def test_inject_skill_records_name_and_version():
    skill = load_skill("research_report")
    plan = inject_skill(ResearchPlan(goal="整理一份研究报告的结构", subjects=["主题"], dimensions=["交付方式"]).model_dump(), skill)
    assert plan["skill_name"] == "research_report"
    assert plan["skill_version"] == skill.version
    assert UNIQUE_BODY["research_report"] not in json.dumps(plan, ensure_ascii=False)


async def test_demo_plan_records_skill_on_plan_and_events(runtime):
    engine, store = runtime
    run = await new_run(engine, store, ["产品 A", "产品 B"])
    plan = store.run(run["id"])["plan"]
    assert plan["skill_name"] == "competitor_analysis"
    assert plan["skill_version"] == load_skill("competitor_analysis").version
    events = store.query("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run["id"],))
    selected = [event for event in events if event["type"] == "skill.selected"]
    proposed = [event for event in events if event["type"] == "plan.proposed"]
    assert selected and selected[0]["payload"]["skill_name"] == "competitor_analysis"
    assert proposed[0]["payload"]["skill_name"] == "competitor_analysis"
    assert proposed[0]["payload"]["skill_version"] == plan["skill_version"]


async def test_live_plan_sees_catalog_then_injects_body(tmp_path):
    store = Store(tmp_path / "skills.sqlite")
    provider = Provider(Settings(_env_file=None, research_mode="live"), store)
    project = store.create_project("技能", "")
    run = store.create_run(project["id"], "比较两家产品的定价", "live", {})
    catalog = skill_catalog()
    catalog_text = json.dumps(catalog, ensure_ascii=False)
    assert UNIQUE_BODY["competitor_analysis"] not in catalog_text
    chosen = choose_skill(run["question"], ["产品 A", "产品 B"], ["定价"], catalog)
    assert chosen == "competitor_analysis"
    seen = []

    async def fake_chat(run_id, messages, key, tools=None):
        seen.append((key, messages[0]["content"], messages[1]["content"]))
        plan = ResearchPlan(goal=run["question"], subjects=["产品 A", "产品 B"], dimensions=["定价"])
        return {"content": plan.model_dump_json()}

    provider.chat = fake_chat
    plan = await provider.plan(run, {"question": run["question"], "subjects": ["产品 A", "产品 B"], "dimensions": ["定价"]})
    assert [item[0] for item in seen] == ["plan"]
    plan_prompt = seen[0][1]
    assert UNIQUE_BODY["competitor_analysis"] in plan_prompt
    assert UNIQUE_BODY["industry_landscape"] not in plan_prompt
    assert "已选择技能 competitor_analysis" in plan_prompt
    assert plan["skill_name"] == "competitor_analysis"
    assert plan["skill_version"] == load_skill("competitor_analysis").version


def test_skill_frontmatter_rejects_unknown_tools(tmp_path):
    path = Path(tmp_path) / "rogue.md"
    path.write_text("""---
name: rogue
version: "1"
title: 越权
when_to_use: 测试
required_dimensions: 定价
allowed_tools: search_web,hack_web
output_schema: {"type":"object"}
---
正文
""", encoding="utf-8")
    with pytest.raises(ValueError, match="未知工具"):
        _skill_from_path(path)
