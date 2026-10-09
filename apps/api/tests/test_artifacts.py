import pytest
from httpx import ASGITransport, AsyncClient
from app.config import Settings
from app.main import create_app
from tests.test_workbench import new_run, start, wait_status

pytest_plugins = ("tests.test_workbench",)


async def complete_run(engine, store):
    run = await new_run(engine, store, ["产品 A"])
    await start(engine, store, run)
    result = await wait_status(store, run["id"], {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    return result


async def test_user_edit_creates_version_and_pending_verification(runtime):
    engine, store = runtime
    calls = []
    original = engine.provider.research

    async def spy(run, plan, subject, round_index, gate, dimensions=None):
        calls.append(subject)
        return await original(run, plan, subject, round_index, gate, dimensions)

    engine.provider.research = spy
    run = await complete_run(engine, store)
    versions = store.list_artifact_versions(run["id"])
    assert len(versions) == 1 and versions[0]["origin"] == "system"
    first = store.get_artifact_version(run["id"], 1)
    claim = first["content"]["claims"][0]
    assert claim["verification"] == "reference_checked"
    evidence_before = store.evidence(run["id"])
    research_calls = len(calls)
    edited = store.add_artifact_version(
        run["id"],
        run["project_id"],
        {
            **first["content"],
            "claims": [{**item, "text": "用户改写后的结论", "verification": "unconfirmed"} if item["id"] == claim["id"] else item for item in first["content"]["claims"]],
        },
        origin="user",
        parent_version_id=first["id"],
    )
    store.update(run["id"], artifact=edited["content"])
    assert edited["version"] == 2
    assert edited["origin"] == "user"
    changed = next(item for item in edited["content"]["claims"] if item["id"] == claim["id"])
    assert changed["text"] == "用户改写后的结论"
    assert changed["verification"] == "unconfirmed"
    assert first["content"]["claims"][0]["text"] != changed["text"]
    assert len(store.evidence(run["id"])) == len(evidence_before)
    assert len(calls) == research_calls
    assert store.run(run["id"])["status"] == "completed"


async def test_api_edit_and_export_chosen_version(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=str(tmp_path), research_mode="demo", max_search_calls=6))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/api/auth/register", json={"username": "editor", "display_name": "编辑者", "password": "test-password-123"})
            project = (await client.post("/api/projects", json={"name": "版本"})).json()
            created = (await client.post(f"/api/projects/{project['id']}/runs", json={"question": "比较产品形态并给出结论", "subjects": ["产品 A"], "dimensions": ["产品形态"]})).json()
            ready = await wait_status(app.state.store, created["id"], {"waiting_input"})
            await client.post(f"/api/runs/{created['id']}/commands", json={"action": "start", "expected_revision": ready["revision"]})
            done = await wait_status(app.state.store, created["id"], {"completed", "failed"})
            assert done["status"] == "completed", done["error"]
            current = (await client.get(f"/api/runs/{created['id']}")).json()
            claim = current["artifact"]["claims"][0]
            patched = await client.patch(f"/api/runs/{created['id']}/artifact", json={
                "expected_revision": current["revision"],
                "base_version": current["artifact_version"],
                "summary": "编辑后的摘要",
                "claims": [{"id": claim["id"], "text": "这是用户改过的结论"}],
            })
            assert patched.status_code == 200, patched.text
            body = patched.json()
            assert body["artifact_version"] == 2
            assert body["artifact"]["summary"] == "编辑后的摘要"
            changed = next(item for item in body["artifact"]["claims"] if item["id"] == claim["id"])
            assert changed["text"] == "这是用户改过的结论"
            assert changed["verification"] == "unconfirmed"
            listing = (await client.get(f"/api/runs/{created['id']}/artifacts")).json()
            assert [item["origin"] for item in listing] == ["system", "user"]
            first = await client.get(f"/api/runs/{created['id']}/export?format=markdown&version=1")
            second = await client.get(f"/api/runs/{created['id']}/export?format=markdown&version=2")
            assert first.status_code == 200 and second.status_code == 200
            assert "这是用户改过的结论" not in first.text
            assert "这是用户改过的结论" in second.text
            assert app.state.store.run(created["id"])["status"] == "completed"
            started = app.state.store.query("SELECT * FROM events WHERE run_id=? AND type='task.started'", (created["id"],))
            edited_at = app.state.store.query("SELECT * FROM events WHERE run_id=? AND type='artifact.edited'", (created["id"],))[0]["seq"]
            assert all(event["seq"] < edited_at for event in started)