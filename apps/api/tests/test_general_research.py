import asyncio
import json
from app.config import Settings
from app.engine import Engine, coverage_for_plan, validate_claims
from app.export import escape_text, safe_url, website_html, website_zip
from app.providers import BudgetExceeded, Provider
from app.research_mode import default_subquestions, infer_mode, needs_clarify
from app.report import attach_report
from app.skills import load_skill
from app.store import Store
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


def test_open_topic_plans_general_tree():
    question = "智能体记忆系统如何工作，领域将往何处去"
    assert infer_mode(question, {}) == "general"
    assert not needs_clarify(question, {})
    tree = default_subquestions(question)
    assert {item["id"] for item in tree} >= {"sq_arch", "sq_short", "sq_long", "sq_practice", "sq_future"}
    assert any(item["parent_id"] == "sq_arch" for item in tree)


def test_comparison_still_grid_and_may_clarify():
    assert infer_mode("比较几个竞品的定价和部署方式", {}) == "grid"
    assert needs_clarify("比较几个竞品的定价和部署方式", {})
    assert infer_mode("调研 ChatGPT、Gemini 和 Claude 的深度研究产品形态，比较研究流程与交付方式。", {}) == "grid"
    assert not needs_clarify("调研 ChatGPT、Gemini 和 Claude 的深度研究产品形态，比较研究流程与交付方式。", {})


def test_demo_planner_emits_general_tree():
    from pathlib import Path
    store = Store(Path("/tmp/general-plan-unused.sqlite"))
    provider = Provider(Settings(_env_file=None, research_mode="demo"), store)
    run = {"id": "run_demo", "question": "智能体记忆系统如何工作，领域将往何处去", "mode": "demo", "project_id": "p"}
    plan = provider.demo_plan(run, {"question": run["question"]}, load_skill("research_report"))
    assert plan["mode"] == "general"
    assert plan["skill_name"] == "research_report"
    assert [item["id"] for item in plan["subquestions"]][0] == "sq_arch"
    store.close()


async def test_followup_loop_stops_when_budget_exceeded(tmp_path):
    store = Store(tmp_path / "followup.sqlite")
    run = store.create_run(store.create_project("跟进", "")["id"], "智能体记忆系统如何工作", "live", {})
    provider = Provider(Settings(_env_file=None, max_model_calls=8, report_reserved_calls=4), store)
    calls = []

    async def fake_search(*args, **kwargs):
        return {"results": [{"title": "文档", "url": "https://example.com/memory", "snippet": "摘要", "raw_content": "Working memory is a short-lived buffer."}]}

    async def gate():
        pass

    answers = iter([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search_web", "arguments": '{"query":"agent memory"}'}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read_source", "arguments": '{"url":"https://example.com/memory"}'}}]},
        {"role": "assistant", "content": "调查完成"},
    ])

    async def sequenced_chat(run_id, messages, key, tools=None):
        calls.append(key)
        if "reflect" in key:
            raise BudgetExceeded("模型调用达到阶段上限")
        return next(answers)

    provider.chat = sequenced_chat
    provider.search = fake_search
    plan = {"mode": "general", "subjects": ["记忆"], "dimensions": ["要点"], "max_search_calls": 6, "max_followup_rounds": 2, "subquestions": default_subquestions("智能体记忆")}
    result = await provider.live_research(run, plan, "sq_arch", 0, gate, ["智能体记忆系统的常见架构有哪些？"])
    assert result["evidence_ids"]
    assert result.get("followups") == []
    follow = store.query("SELECT * FROM events WHERE run_id=? AND type='research.followup'", (run["id"],))
    assert follow == []
    assert any("reflect" in key for key in calls)
    store.close()


async def test_followup_emits_query_when_budget_allows(tmp_path):
    store = Store(tmp_path / "followup-ok.sqlite")
    run = store.create_run(store.create_project("跟进", "")["id"], "智能体记忆系统如何工作", "live", {})
    provider = Provider(Settings(_env_file=None), store)
    answers = iter([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search_web", "arguments": '{"query":"agent memory"}'}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read_source", "arguments": '{"url":"https://example.com/memory"}'}}]},
        {"role": "assistant", "content": "调查完成"},
        {"role": "assistant", "content": json.dumps({"unknowns": ["评测"], "queries": ["记忆评测基准有哪些？"]})},
        {"role": "assistant", "content": "没有更多来源"},
    ])

    async def fake_chat(run_id, messages, key, tools=None):
        return next(answers)

    async def fake_search(*args, **kwargs):
        return {"results": [{"title": "文档", "url": "https://example.com/memory", "snippet": "摘要", "raw_content": "Working memory is a short-lived buffer."}]}

    async def gate():
        pass

    provider.chat = fake_chat
    provider.search = fake_search
    plan = {"mode": "general", "subjects": ["记忆"], "dimensions": ["要点"], "max_search_calls": 6, "max_followup_rounds": 2, "subquestions": default_subquestions("智能体记忆")}
    result = await provider.live_research(run, plan, "sq_arch", 0, gate, ["智能体记忆系统的常见架构有哪些？"])
    assert result["followups"] == ["记忆评测基准有哪些？"]
    follow = store.query("SELECT * FROM events WHERE run_id=? AND type='research.followup'", (run["id"],))
    assert follow[0]["payload"]["query"] == "记忆评测基准有哪些？"
    store.close()


def test_report_structure_from_general_claims():
    plan = {"mode": "general", "goal": "智能体记忆", "subjects": ["记忆"], "dimensions": ["要点"], "subquestions": default_subquestions("智能体记忆")}
    evidence = [{"id": "ev_1", "title": "来源", "url": "https://example.com/a", "quote": "工作记忆是短期缓冲。", "locator": "p1"}]
    claims = [
        {"id": "c1", "subject": "sq_arch", "dimension": "智能体记忆系统的常见架构有哪些？", "text": "常见分层是工作记忆加长期记忆。", "evidence_ids": ["ev_1"], "kind": "analysis", "verification": "reference_checked", "subquestion_id": "sq_arch"},
        {"id": "c2", "subject": "sq_future", "dimension": "未解问题与领域走向是什么？", "text": "未确认：未取得足够证据。", "evidence_ids": [], "kind": "unknown", "verification": "unconfirmed", "subquestion_id": "sq_future"},
    ]
    artifact = attach_report({"title": "智能体记忆", "summary": "摘要", "claims": claims, "mode": "demo"}, plan, evidence, {})
    assert artifact["outline"]
    assert artifact["sections"]
    assert "[[ev_1]]" in artifact["sections"][0]["body"]
    assert artifact["open_questions"]
    assert artifact["sources"][0]["id"] == "ev_1"
    coverage = coverage_for_plan(plan, [{**evidence[0], "task_key": "research:sq_arch:0", "dimensions": ["sq_arch"]}])
    assert coverage["mode"] == "general"
    assert coverage["covered"] >= 1
    filled = validate_claims(plan, {"claims": claims}, evidence)
    assert any(item["subquestion_id"] == "sq_arch" for item in filled)


def test_website_export_escapes_untrusted_text():
    artifact = {
        "title": "<script>alert(1)</script>",
        "summary": "<img src=x onerror=alert(1)>",
        "sections": [{"id": "s1", "title": "<b>坏标题</b>", "body": "正文 <script>alert(1)</script> [[ev_xss]]"}],
        "open_questions": ["<svg onload=alert(1)>"],
        "charts": [{"id": "c1", "title": "<script>x</script>", "kind": "bar", "labels": ["<img>"], "values": [1]}],
        "verification_note": "note",
    }
    evidence = [{"id": "ev_xss", "title": "<script>t</script>", "url": "javascript:alert(1)", "quote": "<script>alert(1)</script>", "locator": "p1"}]
    page = website_html(artifact, evidence, mode="demo")
    assert "<script>" not in page
    assert "<img" not in page
    assert "javascript:" not in page
    assert "&lt;script&gt;" in page
    assert 'href="javascript:' not in page
    assert safe_url("javascript:alert(1)") == ""
    assert safe_url("https://example.com/ok") == "https://example.com/ok"
    assert "&lt;img" in escape_text("<img src=x>")
    zipped = website_zip(artifact, evidence, mode="demo")
    assert zipped.startswith(b"PK")


async def test_demo_general_run_fills_tree_and_report(tmp_path):
    settings = Settings(_env_file=None, data_dir=str(tmp_path), research_mode="demo")
    store = Store(tmp_path / "business.sqlite")
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.sqlite")) as saver:
        engine = Engine(settings, store, saver)
        project = store.create_project("深挖", "")
        request = {"question": "智能体记忆系统如何工作，领域将往何处去", "subjects": [], "dimensions": []}
        run = store.create_run(project["id"], request["question"], "demo", {"request": request})
        engine.schedule(run["id"], {"run_id": run["id"], "request": request})
        async with asyncio.timeout(12):
            while store.run(run["id"])["status"] not in {"waiting_input", "failed"}:
                await asyncio.sleep(0.02)
        planned = store.run(run["id"])
        assert planned["status"] == "waiting_input"
        assert planned["plan"].get("waiting") != "clarify"
        assert planned["plan"]["mode"] == "general"
        assert planned["plan"]["subquestions"]
        store.update(run["id"], status="running")
        engine.schedule(run["id"], await engine.resume_input(run["id"]))
        async with asyncio.timeout(20):
            while store.run(run["id"])["status"] not in {"completed", "partial", "failed"}:
                await asyncio.sleep(0.05)
        done = store.run(run["id"])
        assert done["status"] in {"completed", "partial"}
        artifact = done["artifact"]
        assert artifact["sections"]
        assert artifact["open_questions"]
        assert artifact["sources"]
        follow = store.query("SELECT * FROM events WHERE run_id=? AND type='research.followup'", (run["id"],))
        assert follow
        coverage = done["artifact"]["coverage"]
        assert coverage["mode"] == "general"
        assert coverage["covered"] == coverage["total"]
        await engine.shutdown()
    store.close()
