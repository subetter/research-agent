import asyncio
import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from app.config import Settings
from app.store import Store
from app.main import create_app
from app.engine import Engine, validate_claims
from app.schemas import ResearchPlan


def config(tmp_path):
    return Settings(_env_file=None, data_dir=str(tmp_path), research_mode="demo", max_search_calls=6)


async def wait_status(store, run_id, statuses, timeout=10):
    async with asyncio.timeout(timeout):
        while True:
            run = store.run(run_id)
            if run["status"] in statuses:
                return run
            await asyncio.sleep(0.02)


async def new_run(engine, store, subjects=None, parent=None):
    project = store.create_project("测试项目", "背景") if parent is None else store.project(parent["project_id"])
    request = {"question": "比较研究对象的产品形态和流程", "subjects": subjects or ["产品 A", "产品 B"], "dimensions": ["产品形态", "研究流程"]}
    run = store.create_run(project["id"], request["question"], "demo", {"request": request}, parent["id"] if parent else None)
    engine.schedule(run["id"], {"run_id": run["id"], "request": request})
    return await wait_status(store, run["id"], {"waiting_input", "failed"})


async def start(engine, store, run):
    await asyncio.sleep(0)
    input = await engine.resume_input(run["id"])
    store.update(run["id"], status="running")
    engine.schedule(run["id"], input)


@pytest.fixture
async def runtime(tmp_path):
    settings = config(tmp_path)
    store = Store(tmp_path / "business.sqlite")
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.sqlite")) as saver:
        engine = Engine(settings, store, saver)
        yield engine, store
        await engine.shutdown()
    store.close()


def test_revision_and_budget_are_atomic(tmp_path):
    store = Store(tmp_path / "store.sqlite")
    p = store.create_project("项目", "")
    run = store.create_run(p["id"], "研究问题", "demo", {})
    store.update(run["id"], expected_revision=0, status="waiting_input")
    with pytest.raises(ValueError):
        store.update(run["id"], expected_revision=0, status="running")
    assert store.reserve(run["id"], "search1", "search", 1)
    assert not store.reserve(run["id"], "search2", "search", 1)
    assert not store.reserve(run["id"], "search1", "search", 1)
    store.close()


def test_invalid_reference_cannot_be_published_as_fact():
    plan = {"subjects": ["A"], "dimensions": ["价格", "功能"]}
    bundle = {"claims": [{"subject": "A", "dimension": "价格", "text": "免费", "kind": "fact", "evidence_ids": ["invented"]}]}
    result = validate_claims(plan, bundle, [])
    assert len(result) == 2
    assert all(c["kind"] == "unknown" for c in result)
    assert all(not c["evidence_ids"] for c in result)


def test_plan_rejects_empty_subjects():
    with pytest.raises(ValueError):
        ResearchPlan(goal="一个完整研究目标", subjects=[" "], dimensions=["功能"])


async def test_full_graph_with_edited_plan(runtime):
    engine, store = runtime
    run = await new_run(engine, store)
    assert run["status"] == "waiting_input"
    plan = {**run["plan"], "subjects": ["修改后的产品"], "dimensions": ["部署方式"]}
    store.update(run["id"], plan=plan)
    await start(engine, store, run)
    result = await wait_status(store, run["id"], {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    assert len(result["artifact"]["claims"]) == 1
    assert result["artifact"]["claims"][0]["subject"] == "修改后的产品"
    assert store.evidence(run["id"])[0]["source_type"] == "demo"
    events = store.query("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run["id"],))
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


async def test_pause_and_resume_without_duplicate_evidence(runtime):
    engine, store = runtime
    run = await new_run(engine, store)
    await start(engine, store, run)
    await asyncio.sleep(0.4)
    store.update(run["id"], status="pause_requested")
    await wait_status(store, run["id"], {"paused"})
    await asyncio.sleep(0)
    store.update(run["id"], status="running")
    engine.schedule(run["id"], await engine.resume_input(run["id"]))
    result = await wait_status(store, run["id"], {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    assert len(store.evidence(run["id"])) == 4


async def test_incremental_research_reuses_parent(runtime):
    engine, store = runtime
    first = await new_run(engine, store, ["产品 A"])
    await start(engine, store, first)
    parent = await wait_status(store, first["id"], {"completed"})
    child = await new_run(engine, store, ["产品 A", "产品 B"], parent)
    await start(engine, store, child)
    result = await wait_status(store, child["id"], {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    reused = store.query("SELECT * FROM events WHERE run_id=? AND type='task.reused'", (child["id"],))
    assert any(e["payload"]["subject"] == "产品 A" for e in reused)
    assert len(store.evidence(child["id"])) == 4


async def test_restart_recovers_checkpoint(tmp_path):
    settings = config(tmp_path)
    store = Store(tmp_path / "business.sqlite")
    graph_path = str(tmp_path / "graph.sqlite")
    async with AsyncSqliteSaver.from_conn_string(graph_path) as saver:
        engine = Engine(settings, store, saver)
        run = await new_run(engine, store)
        await start(engine, store, run)
        await asyncio.sleep(0.4)
        await engine.shutdown()
    assert store.run(run["id"])["status"] == "paused"
    async with AsyncSqliteSaver.from_conn_string(graph_path) as saver:
        restarted = Engine(settings, store, saver)
        store.update(run["id"], status="running")
        restarted.schedule(run["id"], await restarted.resume_input(run["id"]))
        result = await wait_status(store, run["id"], {"completed", "failed"})
        assert result["status"] == "completed", result["error"]
        assert len(store.evidence(run["id"])) == 4
        await restarted.shutdown()
    store.close()


async def test_cancel_does_not_generate_artifact(runtime):
    engine, store = runtime
    run = await new_run(engine, store)
    await start(engine, store, run)
    await asyncio.sleep(0.1)
    store.update(run["id"], status="cancel_requested")
    result = await wait_status(store, run["id"], {"cancelled"})
    assert result["artifact"] is None


async def test_api_validation_upload_and_export(tmp_path):
    app = create_app(config(tmp_path))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/api/auth/register", json={"username": "test_admin", "display_name": "测试管理员", "password": "test-password-123"})
            p = (await client.post("/api/projects", json={"name": "API 测试"})).json()
            bad = await client.post(f"/api/projects/{p['id']}/runs", json={"question": "短"})
            assert bad.status_code == 422
            upload = await client.post(f"/api/projects/{p['id']}/documents", files={"file": ("note.md", "背景资料".encode(), "text/markdown")})
            assert upload.status_code == 201
            forbidden = await client.post(f"/api/projects/{p['id']}/documents", files={"file": ("note.pdf", b"pdf", "application/pdf")})
            assert forbidden.status_code == 415
            run = (await client.post(f"/api/projects/{p['id']}/runs", json={"question": "调查产品形态并给出对比", "subjects": ["A"], "dimensions": ["功能"]})).json()
            ready = await wait_status(app.state.store, run["id"], {"waiting_input"})
            conflict = await client.post(f"/api/runs/{run['id']}/commands", json={"action": "start", "expected_revision": -1})
            assert conflict.status_code == 409
            started = await client.post(f"/api/runs/{run['id']}/commands", json={"action": "start", "expected_revision": ready["revision"]})
            assert started.status_code == 202
            await wait_status(app.state.store, run["id"], {"completed"})
            csv = await client.get(f"/api/runs/{run['id']}/export?format=csv")
            assert csv.status_code == 200 and "模拟" in csv.text
            trace = await client.get(f"/api/runs/{run['id']}/trace")
            last_seq = trace.json()[-1]["seq"]
            sse = await client.get(f"/api/runs/{run['id']}/events?after={last_seq-1}")
            assert f"id: {last_seq}" in sse.text
            denied = await client.post("/api/projects", headers={"Origin": "https://evil.example"}, json={"name": "bad"})
            assert denied.status_code == 403


async def test_live_mode_requires_keys_without_fallback(tmp_path):
    settings = config(tmp_path)
    settings.research_mode = "live"
    settings.llm_api_key = ""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/api/auth/register", json={"username": "test_admin", "display_name": "测试管理员", "password": "test-password-123"})
            p = (await client.post("/api/projects", json={"name": "live"})).json()
            result = await client.post(f"/api/projects/{p['id']}/runs", json={"question": "一个真实研究问题"})
            assert result.status_code == 503
