import asyncio
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from app.config import Settings
from app.engine import Engine
from app.main import create_app
from app.scope import infer_scope, needs_clarify
from app.store import Store


def test_infer_scope_skips_clarify_when_question_has_both():
    subjects, dimensions = infer_scope("调研 ChatGPT、Gemini 和 Claude 的深度研究产品形态，比较研究流程与交付方式。", {})
    assert subjects == ["ChatGPT", "Gemini", "Claude"]
    assert "产品形态" in dimensions and "研究流程" in dimensions
    assert not needs_clarify("调研 ChatGPT、Gemini 和 Claude 的深度研究产品形态，比较研究流程与交付方式。", {})


def test_vague_question_needs_clarify():
    assert needs_clarify("帮我做一份行业观察，看看最近有什么值得关注的。", {"subjects": [], "dimensions": []})


async def wait_status(store, run_id, statuses, timeout=10):
    async with asyncio.timeout(timeout):
        while True:
            run = store.run(run_id)
            if run["status"] in statuses:
                return run
            await asyncio.sleep(0.02)


async def test_clarify_interrupt_only_when_scope_missing(tmp_path):
    settings = Settings(_env_file=None, data_dir=str(tmp_path), research_mode="demo")
    store = Store(tmp_path / "business.sqlite")
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.sqlite")) as saver:
        engine = Engine(settings, store, saver)
        project = store.create_project("澄清", "")
        request = {"question": "帮我做一份行业观察，看看最近有什么值得关注的。", "subjects": [], "dimensions": []}
        run = store.create_run(project["id"], request["question"], "demo", {"request": request})
        engine.schedule(run["id"], {"run_id": run["id"], "request": request})
        ready = await wait_status(store, run["id"], {"waiting_input", "failed"})
        assert ready["status"] == "waiting_input"
        assert ready["plan"]["waiting"] == "clarify"
        assert "研究对象或比较维度" in ready["plan"]["clarify_question"]
        scoped = {**ready["plan"], "request": {**request, "subjects": ["产品 A"], "dimensions": ["产品形态"]}}
        store.update(run["id"], plan=scoped, status="running")
        engine.schedule(run["id"], await engine.resume_input(run["id"]))
        planned = await wait_status(store, run["id"], {"waiting_input", "failed"})
        assert planned["status"] == "waiting_input"
        assert planned["plan"].get("waiting") != "clarify"
        assert planned["plan"]["subjects"] == ["产品 A"]
        assert planned["plan"]["as_of"]
        assert planned["plan"]["regions"] == ["未限定"]
        await engine.shutdown()
    store.close()


async def test_provided_scope_goes_to_plan_card(tmp_path):
    settings = Settings(_env_file=None, data_dir=str(tmp_path), research_mode="demo")
    store = Store(tmp_path / "business.sqlite")
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.sqlite")) as saver:
        engine = Engine(settings, store, saver)
        project = store.create_project("澄清", "")
        request = {"question": "比较产品 A 与产品 B 的产品形态", "subjects": ["产品 A", "产品 B"], "dimensions": ["产品形态"]}
        run = store.create_run(project["id"], request["question"], "demo", {"request": request})
        engine.schedule(run["id"], {"run_id": run["id"], "request": request})
        ready = await wait_status(store, run["id"], {"waiting_input", "failed"})
        assert ready["plan"].get("waiting") != "clarify"
        assert ready["plan"]["subjects"] == ["产品 A", "产品 B"]
        assert ready["plan"]["as_of"]
        await engine.shutdown()
    store.close()


async def test_clarify_http_then_plan_defaults(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=str(tmp_path), research_mode="demo"))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/api/auth/register", json={"username": "test_admin", "display_name": "测试", "password": "test-password-123"})
            project = (await client.post("/api/projects", json={"name": "澄清接口"})).json()
            created = await client.post(f"/api/projects/{project['id']}/runs", json={"question": "帮我看看最近行业里发生了什么"})
            run = created.json()
            ready = await wait_status(app.state.store, run["id"], {"waiting_input"})
            body = (await client.get(f"/api/runs/{run['id']}")).json()
            assert body["waiting"] == "clarify"
            updated = await client.put(f"/api/runs/{run['id']}/clarify", json={"expected_revision": ready["revision"], "subjects": ["产品 A"], "dimensions": ["产品形态"]})
            assert updated.status_code == 200
            started = await client.post(f"/api/runs/{run['id']}/commands", json={"action": "start", "expected_revision": updated.json()["revision"]})
            assert started.status_code == 202
            planned = await wait_status(app.state.store, run["id"], {"waiting_input"})
            assert planned["plan"]["subjects"] == ["产品 A"]
            assert planned["plan"]["regions"] == ["未限定"]
            assert planned["plan"].get("waiting") != "clarify"
