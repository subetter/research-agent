import asyncio

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import Settings
from app.engine import Engine
from app.progress import map_progress
from app.records import sanitize_record_payload, write_record
from app.report_markdown import render_report_markdown, partial_markdown
from app.secrets import redact_payload
from app.store import Store


def ev(seq, type, **payload):
    return {"seq": seq, "type": type, "payload": payload, "created_at": "2026-10-13T00:00:00+00:00"}


def test_progress_waiting_for_plan():
    events = [
        ev(1, "run.created"),
        ev(2, "stage.started", stage="plan"),
        ev(3, "plan.started"),
        ev(4, "plan.proposed", subjects=["A"], dimensions=["定价"]),
        ev(5, "run.waiting_input"),
    ]
    progress = map_progress(events, {"status": "waiting_input", "created_at": "2026-10-13T00:00:00+00:00"})
    states = {item["id"]: item["state"] for item in progress["stages"]}
    assert states["plan"] == "done"
    assert states["approve"] == "waiting"
    assert states["research"] == "pending"
    assert progress["current"] == "approve"
    assert "确认" in progress["activity"]
    assert states["supplement"] == "skipped"


def test_progress_mid_research_and_supplement():
    events = [
        ev(1, "plan.proposed"),
        ev(2, "run.running"),
        ev(3, "task.started", subject="ChatGPT", round=0),
        ev(4, "tool.started", tool="search_web", query="ChatGPT 定价", subject="ChatGPT"),
    ]
    mid = map_progress(events, {"status": "running", "created_at": "2026-10-13T00:00:00+00:00"})
    states = {item["id"]: item["state"] for item in mid["stages"]}
    assert states["research"] == "current"
    assert "正在搜索 ChatGPT 定价" in mid["activity"]

    events += [
        ev(5, "coverage.checked", will_retry=True, round=0, conflicts=[]),
        ev(6, "task.started", subject="ChatGPT", round=1),
    ]
    later = map_progress(events, {"status": "running", "created_at": "2026-10-13T00:00:00+00:00"})
    states = {item["id"]: item["state"] for item in later["stages"]}
    assert states["research"] == "done"
    assert states["gap_check"] == "done"
    assert states["supplement"] == "current"
    assert "补充" in later["activity"]


def test_progress_completed_and_failed():
    done_events = [ev(1, "plan.proposed"), ev(2, "coverage.checked", will_retry=False), ev(3, "report.started"), ev(4, "claims.validated"), ev(5, "run.completed")]
    done = map_progress(done_events, {"status": "completed", "created_at": "2026-10-13T00:00:00+00:00", "updated_at": "2026-10-13T00:02:00+00:00"})
    assert all(item["state"] in {"done", "skipped"} for item in done["stages"])
    assert done["elapsed_seconds"] == 120

    failed = map_progress([ev(1, "plan.started")], {"status": "failed", "created_at": "2026-10-13T00:00:00+00:00"})
    assert {item["id"]: item["state"] for item in failed["stages"]}["plan"] == "failed"


def test_markdown_tables_citations_and_pdf_page():
    run = {
        "question": "比较定价",
        "mode": "demo",
        "status": "completed",
        "plan": {"subjects": ["InkBot", "ChatGPT"], "dimensions": ["定价"]},
        "artifact": {
            "title": "比较定价",
            "summary": "演示摘要",
            "verification_note": "仅校验引用结构。",
            "claims": [
                {"subject": "InkBot", "dimension": "定价", "text": "官方口径为 20 美元", "kind": "analysis", "evidence_ids": ["ev1"], "conflict_evidence_ids": ["ev2"]},
                {"subject": "ChatGPT", "dimension": "定价", "text": "未取得足够证据", "kind": "unknown", "evidence_ids": [], "conflict_evidence_ids": []},
            ],
            "conflicts": [{
                "subject": "InkBot",
                "dimension": "定价",
                "positions": [
                    {"label": "官方", "text": "20 美元", "evidence_ids": ["ev1"], "freshness": "stale", "freshness_label": "过时"},
                    {"label": "评测", "text": "42 美元", "evidence_ids": ["ev2"], "freshness": "fresh", "freshness_label": "较新"},
                ],
            }],
            "metrics": {"claims": 2, "evidence": 2, "unknown": 1, "conflicts": 1, "fresh_evidence": 1, "stale_evidence": 1, "undated_evidence": 0},
        },
    }
    evidence = [
        {"id": "ev1", "title": "InkBot 手册", "url": "project://doc1#page=2", "locator": "page:2;paragraph:1;chars:0-20", "source_type": "document", "freshness_label": "未标注日期"},
        {"id": "ev2", "title": "第三方评测", "url": "https://example.com/review", "locator": "paragraph:review", "source_type": "demo", "freshness_label": "较新"},
    ]
    markdown = render_report_markdown(run, evidence)
    assert markdown.startswith("# 比较定价")
    assert "| 维度 | InkBot | ChatGPT |" in markdown
    assert "| --- | --- | --- |" in markdown
    assert "[1]" in markdown and "[2]" in markdown
    assert "[第三方评测](https://example.com/review)" in markdown
    assert "第 2 页" in markdown
    assert "## 来源冲突" in markdown
    assert "过时" in markdown
    stub = partial_markdown({"question": "未完成", "status": "failed", "plan": {}}, evidence)
    assert "部分成果" in stub
    assert "参考文献" in stub


def test_records_redact_secrets_and_truncate(tmp_path):
    store = Store(tmp_path / "records.sqlite")
    project = store.create_project("记录", "")
    run = store.create_run(project["id"], "研究密钥脱敏", "demo", {})
    secret = "sk-live-secret-value-99999"
    settings = Settings(_env_file=None, llm_api_key=secret)
    payload = {
        "request": {"headers": {"Authorization": f"Bearer {secret}"}, "api_key": secret, "messages": [{"role": "user", "content": f"key={secret}"}]},
        "response": {"content": "ok " + ("很长的正文" * 4000)},
    }
    preview, full, truncated = sanitize_record_payload(payload, settings)
    dumped = str(preview) + str(full)
    assert secret not in dumped
    assert "[REDACTED]" in dumped
    assert truncated is True
    assert "已截断" in str(preview) or preview.get("_truncated") or (isinstance(preview.get("response"), dict) and "已截断" in str(preview.get("response")))

    record_id = write_record(
        store,
        run["id"],
        kind="llm",
        operation_key="plan",
        stage="plan",
        name="demo-fixture",
        role="plan",
        preview={"model": "demo-fixture"},
        payload=payload,
        settings=settings,
    )
    stored = store.execution_record(record_id)
    assert secret not in str(stored["preview"]) and secret not in str(stored["payload"])
    assert stored["truncated"] is True
    listed = store.execution_records(run["id"])
    assert listed and listed[0]["id"] == record_id
    store.close()


def test_redact_payload_strips_auth_keys():
    clean = redact_payload({"authorization": "Bearer abcdefghijkl", "nested": {"tavily_api_key": "tvly-secret-key"}})
    assert clean["authorization"] == "[REDACTED]"
    assert clean["nested"]["tavily_api_key"] == "[REDACTED]"


async def test_demo_run_persists_records_and_markdown(tmp_path):
    settings = Settings(_env_file=None, data_dir=str(tmp_path), research_mode="demo", max_search_calls=6)
    store = Store(tmp_path / "business.sqlite")
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.sqlite")) as saver:
        engine = Engine(settings, store, saver)
        project = store.create_project("进度", "")
        request = {"question": "比较研究对象的产品形态和流程", "subjects": ["产品 A"], "dimensions": ["产品形态"]}
        run = store.create_run(project["id"], request["question"], "demo", {"request": request})
        engine.schedule(run["id"], {"run_id": run["id"], "request": request})
        async with asyncio.timeout(10):
            while store.run(run["id"])["status"] not in {"waiting_input", "failed"}:
                await asyncio.sleep(0.02)
        waiting = store.run(run["id"])
        assert waiting["status"] == "waiting_input"
        events = store.query("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run["id"],))
        progress = map_progress(events, waiting)
        assert progress["current"] == "approve"
        assert any(item["kind"] == "llm" and item["name"] == "demo-fixture" for item in store.execution_records(run["id"]))
        await asyncio.sleep(0)
        store.update(run["id"], status="running")
        engine.schedule(run["id"], await engine.resume_input(run["id"]))
        async with asyncio.timeout(10):
            while store.run(run["id"])["status"] not in {"completed", "failed"}:
                await asyncio.sleep(0.02)
        result = store.run(run["id"])
        assert result["status"] == "completed", result.get("error")
        assert result["artifact"]["markdown"]
        assert "| 维度 | 产品 A |" in result["artifact"]["markdown"]
        records = store.execution_records(run["id"])
        kinds = {item["kind"] for item in records}
        assert "llm" in kinds and "tool" in kinds
        assert any(item["name"] == "demo_sources" for item in records)
        events = store.query("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run["id"],))
        done = map_progress(events, result)
        assert done["current"] == "report"
        assert all(item["state"] in {"done", "skipped"} for item in done["stages"])
        await engine.shutdown()
    store.close()
