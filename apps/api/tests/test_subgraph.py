import json
from app.config import Settings
from app.engine import Engine
from app.store import Store
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from tests.test_workbench import new_run, start, wait_status


def config(tmp_path):
    return Settings(_env_file=None, data_dir=str(tmp_path), research_mode="demo", max_search_calls=6, max_researchers=1)


async def wait_finished_subject(engine, run_id, subjects, timeout=10):
    import asyncio
    async with asyncio.timeout(timeout):
        while True:
            for subject in subjects:
                snapshot = await engine.researcher.aget_state(engine.researcher_config(run_id, subject, 0))
                if snapshot.values and not snapshot.next and snapshot.values.get("result"):
                    return subject
            await asyncio.sleep(0.02)


async def test_resume_after_crash_skips_finished_subject(tmp_path):
    settings = config(tmp_path)
    store = Store(tmp_path / "business.sqlite")
    graph_path = str(tmp_path / "graph.sqlite")
    calls = []
    async with AsyncSqliteSaver.from_conn_string(graph_path) as saver:
        engine = Engine(settings, store, saver)
        original = engine.provider.research

        async def spy(run, plan, subject, round_index, gate, dimensions=None):
            calls.append((subject, round_index))
            return await original(run, plan, subject, round_index, gate, dimensions)

        engine.provider.research = spy
        run = await new_run(engine, store)
        await start(engine, store, run)
        finished = await wait_finished_subject(engine, run["id"], run["plan"]["subjects"])
        await engine.shutdown()
    assert store.run(run["id"])["status"] == "paused"
    first_calls = list(calls)
    assert (finished, 0) in first_calls
    async with AsyncSqliteSaver.from_conn_string(graph_path) as saver:
        restarted = Engine(settings, store, saver)
        original = restarted.provider.research

        async def spy_again(run, plan, subject, round_index, gate, dimensions=None):
            calls.append((subject, round_index))
            return await original(run, plan, subject, round_index, gate, dimensions)

        restarted.provider.research = spy_again
        store.update(run["id"], status="running")
        restarted.schedule(run["id"], await restarted.resume_input(run["id"]))
        result = await wait_status(store, run["id"], {"completed", "failed"})
        assert result["status"] == "completed", result["error"]
        assert (finished, 0) not in calls[len(first_calls):]
        reused = store.query("SELECT * FROM events WHERE run_id=? AND type='task.reused'", (run["id"],))
        assert any(event["payload"].get("subject") == finished and "子图" in (event["payload"].get("reason") or "") for event in reused)
        assert len(store.evidence(run["id"])) == 4
        parent = await restarted.graph.aget_state({"configurable": {"thread_id": run["id"]}})
        blob = json.dumps(parent.values.get("results") or [], ensure_ascii=False)
        assert "quote" not in blob and "模拟资料" not in blob
        assert all(item.get("evidence_ids") and "summary" in item for item in parent.values.get("results") or [])
        await restarted.shutdown()
    store.close()
