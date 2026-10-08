import asyncio
from app.config import Settings
from app.engine import Engine
from app.providers import Provider
from app.store import Store
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


def settings(tmp_path):
    return Settings(_env_file=None, data_dir=str(tmp_path), research_mode="demo")


async def test_demo_fills_coverage_cells_with_subject_events(tmp_path):
    store = Store(tmp_path / "business.sqlite")
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.sqlite")) as saver:
        engine = Engine(settings(tmp_path), store, saver)
        project = store.create_project("进度", "")
        request = {"question": "比较产品 A 与产品 B 的产品形态", "subjects": ["产品 A", "产品 B"], "dimensions": ["产品形态"]}
        run = store.create_run(project["id"], request["question"], "demo", {"request": request})
        engine.schedule(run["id"], {"run_id": run["id"], "request": request})
        async with asyncio.timeout(10):
            while store.run(run["id"])["status"] not in {"waiting_input", "failed"}:
                await asyncio.sleep(0.02)
        store.update(run["id"], status="running")
        engine.schedule(run["id"], await engine.resume_input(run["id"]))
        async with asyncio.timeout(10):
            while store.run(run["id"])["status"] not in {"completed", "failed"}:
                await asyncio.sleep(0.02)
        assert store.run(run["id"])["status"] == "completed"
        created = store.query("SELECT * FROM events WHERE run_id=? AND type='evidence.created'", (run["id"],))
        assert created
        assert all(event["payload"].get("subject") in {"产品 A", "产品 B"} for event in created)
        filled = store.query("SELECT * FROM events WHERE run_id=? AND type='coverage.cell_filled'", (run["id"],))
        assert {event["payload"]["dimension"] for event in filled} == {"产品形态"}
        assert {event["payload"]["subject"] for event in filled} == {"产品 A", "产品 B"}
        assert not any(event["type"].startswith("tool.") and event["payload"].get("tool") == "search_web" for event in store.query("SELECT * FROM events WHERE run_id=?", (run["id"],)))
        await engine.shutdown()
    store.close()


async def test_live_tool_events_include_truncated_query_and_url(tmp_path):
    store = Store(tmp_path / "tools.sqlite")
    run = store.create_run(store.create_project("进度", "")["id"], "研究产品部署方式", "live", {})
    provider = Provider(Settings(_env_file=None), store)
    query = "A 部署方式 官方文档 " + ("补充说明" * 20)
    answers = iter([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search_web", "arguments": '{"query":"%s"}' % query}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read_source", "arguments": '{"url":"https://example.com/product"}'}}]},
        {"role": "assistant", "content": "调查完成"},
    ])

    async def fake_chat(*args, **kwargs):
        return next(answers)

    async def fake_search(*args, **kwargs):
        return {"results": [{"title": "官方文档", "url": "https://example.com/product", "snippet": "摘要", "raw_content": "部署方式：支持团队私有化安装。"}]}

    async def gate():
        pass

    provider.chat = fake_chat
    provider.search = fake_search
    plan = {"subjects": ["A"], "dimensions": ["部署方式"], "max_search_calls": 3}
    result = await provider.live_research(run, plan, "A", 0, gate)
    assert result["evidence_ids"]
    started = store.query("SELECT * FROM events WHERE run_id=? AND type='tool.started'", (run["id"],))
    search = next(event for event in started if event["payload"]["tool"] == "search_web")
    read = next(event for event in started if event["payload"]["tool"] == "read_source")
    assert search["payload"]["subject"] == "A"
    assert search["payload"]["target"].startswith("A 部署方式")
    assert search["payload"]["target"].endswith("…")
    assert read["payload"]["url"] == "https://example.com/product"
    filled = store.query("SELECT * FROM events WHERE run_id=? AND type='coverage.cell_filled'", (run["id"],))
    assert filled[0]["payload"]["subject"] == "A"
    assert filled[0]["payload"]["dimension"] == "部署方式"
    store.close()
