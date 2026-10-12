from datetime import datetime, timezone

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.conflicts import assess_gaps, detect_conflicts, extract_signals, signals_conflict
from app.engine import Engine, validate_claims
from app.freshness import can_reuse_evidence, classify_freshness, parse_published_at
from app.schemas import ResearchPlan
from app.store import Store

from tests.test_workbench import config, new_run, start, wait_status


@pytest.fixture
async def runtime(tmp_path):
    settings = config(tmp_path)
    store = Store(tmp_path / "business.sqlite")
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.sqlite")) as saver:
        engine = Engine(settings, store, saver)
        yield engine, store
        await engine.shutdown()
    store.close()


def _evidence(store, project_id, run_id, subject, dimension, quote, published_at=None, locator="p1", url=None):
    return store.add_evidence(project_id, run_id, f"research:{subject}:0", {
        "title": f"{subject} · {dimension} · 来源",
        "url": url or f"https://example.com/{locator}",
        "quote": quote,
        "locator": locator,
        "source_type": "web",
        "dimension": dimension,
        "published_at": published_at,
        "fetched_at": "2026-10-12T00:00:00+00:00",
    })


def test_published_at_is_never_taken_from_fetched_at(tmp_path):
    store = Store(tmp_path / "fresh.sqlite")
    project = store.create_project("时效", "")
    run = store.create_run(project["id"], "研究定价时效", "demo", {})
    evidence_id = store.add_evidence(project["id"], run["id"], "research:A:0", {
        "title": "A · 定价",
        "url": "https://example.com/a",
        "quote": "官方定价 20 美元",
        "locator": "p1",
        "source_type": "web",
        "fetched_at": "2026-10-12T08:00:00+00:00",
    })
    row = next(item for item in store.evidence(run["id"]) if item["id"] == evidence_id)
    assert row["fetched_at"] == "2026-10-12T08:00:00+00:00"
    assert row["published_at"] is None
    assert parse_published_at(None) is None
    assert parse_published_at("") is None
    assert classify_freshness(None, fetched_at=row["fetched_at"]) == "unknown"
    store.close()


def test_freshness_undated_dated_and_stale():
    assert classify_freshness(None, as_of="2026-10-01", max_age_days=180) == "unknown"
    assert classify_freshness("2026-09-01", as_of="2026-10-01", max_age_days=180) == "fresh"
    assert classify_freshness("2024-01-01", as_of="2026-10-01", max_age_days=365) == "stale"
    assert parse_published_at("2024-03-15T00:00:00.000Z").startswith("2024-03-15")
    assert parse_published_at("not-a-date") is None


def test_reuse_ignores_fetch_time_and_respects_publish_time():
    stale = {"published_at": "2020-01-01", "fetched_at": datetime.now(timezone.utc).isoformat()}
    undated = {"published_at": None, "fetched_at": datetime.now(timezone.utc).isoformat()}
    fresh = {"published_at": "2026-09-01", "fetched_at": "2018-01-01T00:00:00+00:00"}
    plan = {"as_of": "2026-10-01", "max_evidence_age_days": 365, "require_freshness": True}
    assert can_reuse_evidence(stale, plan, "demo") is True
    assert can_reuse_evidence(stale, plan, "live") is False
    assert can_reuse_evidence(undated, plan, "live") is False
    assert can_reuse_evidence(fresh, plan, "live") is True
    lax = {"require_freshness": False, "max_evidence_age_days": 365, "as_of": "2026-10-01"}
    assert can_reuse_evidence(undated, lax, "live") is True
    assert can_reuse_evidence(stale, lax, "live") is False


def test_conflict_detection_on_incompatible_prices(tmp_path):
    store = Store(tmp_path / "conflict.sqlite")
    project = store.create_project("冲突", "")
    run = store.create_run(project["id"], "比较定价", "demo", {})
    _evidence(store, project["id"], run["id"], "ChatGPT", "定价", "官方公布定价为每月 20 美元。", "2020-03-01", "official")
    _evidence(store, project["id"], run["id"], "ChatGPT", "定价", "第三方评测称定价为每月 42 美元。", "2026-09-01", "review")
    plan = {"subjects": ["ChatGPT"], "dimensions": ["定价"], "require_freshness": False}
    conflicts = detect_conflicts(plan, store.evidence(run["id"]))
    assert len(conflicts) == 1
    assert conflicts[0]["subject"] == "ChatGPT"
    assert conflicts[0]["dimension"] == "定价"
    assert len(conflicts[0]["positions"]) == 2
    ids = {evidence_id for position in conflicts[0]["positions"] for evidence_id in position["evidence_ids"]}
    assert len(ids) == 2
    store.close()


def test_polarity_and_free_vs_paid_conflict():
    assert signals_conflict(extract_signals("该产品支持团队部署"), extract_signals("该产品不支持团队部署"))
    assert signals_conflict(extract_signals("个人版免费使用"), extract_signals("个人版开始收费，订阅制"))
    assert not signals_conflict(extract_signals("官方定价 20 美元"), extract_signals("官网仍写每月 20 美元"))


def test_gap_check_flags_conflicts_and_stale_unknown(tmp_path):
    store = Store(tmp_path / "gaps.sqlite")
    project = store.create_project("缺口", "")
    run = store.create_run(project["id"], "比较定价", "demo", {})
    _evidence(store, project["id"], run["id"], "A", "定价", "官方定价 20 美元。", "2020-01-01", "a1")
    _evidence(store, project["id"], run["id"], "A", "定价", "评测定价 42 美元。", "2020-02-01", "a2")
    _evidence(store, project["id"], run["id"], "B", "定价", "未写日期的定价说明。", None, "b1")
    plan = {"subjects": ["A", "B"], "dimensions": ["定价"], "as_of": "2026-10-01", "max_evidence_age_days": 180, "require_freshness": True}
    assessment = assess_gaps(plan, store.evidence(run["id"]))
    assert assessment["conflict_count"] == 1
    assert "A" in assessment["gaps"]
    assert "B" in assessment["gaps"]
    assert assessment["undated_evidence"] == 1
    assert assessment["stale_evidence"] >= 2
    store.close()


def test_claim_evidence_persists_support_and_conflict(tmp_path):
    store = Store(tmp_path / "links.sqlite")
    project = store.create_project("关系", "")
    run = store.create_run(project["id"], "比较定价", "demo", {})
    support = _evidence(store, project["id"], run["id"], "A", "定价", "官方定价 20 美元。", "2026-01-01", "s")
    opposed = _evidence(store, project["id"], run["id"], "A", "定价", "评测定价 42 美元。", "2026-01-02", "c")
    plan = {"subjects": ["A"], "dimensions": ["定价"]}
    bundle = {"claims": [{"subject": "A", "dimension": "定价", "text": "两边口径不一致", "kind": "analysis", "evidence_ids": [support], "conflict_evidence_ids": [opposed]}]}
    claims = validate_claims(plan, bundle, store.evidence(run["id"]))
    store.replace_claim_evidence(run["id"], [
        {"claim_id": claims[0]["id"], "evidence_id": support, "relation": "support"},
        {"claim_id": claims[0]["id"], "evidence_id": opposed, "relation": "conflict"},
    ])
    rows = store.claim_evidence(run["id"])
    assert {row["relation"] for row in rows} == {"support", "conflict"}
    assert {row["evidence_id"] for row in rows} == {support, opposed}
    store.close()


async def test_demo_conflict_fixture_and_api_payload(runtime):
    engine, store = runtime
    run = await new_run(engine, store, ["InkBot"])
    plan = {**run["plan"], "subjects": ["InkBot"], "dimensions": ["定价"], "as_of": "2026-10-01", "max_evidence_age_days": 365, "require_freshness": False, "max_gap_rounds": 0}
    store.update(run["id"], plan=plan)
    await start(engine, store, run)
    result = await wait_status(store, run["id"], {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    evidence = store.evidence(result["id"])
    assert len(evidence) == 2
    assert all(item["published_at"] for item in evidence)
    assert all(item["published_at"] != item["fetched_at"] for item in evidence)
    artifact = result["artifact"]
    assert artifact["conflicts"]
    assert artifact["metrics"]["conflicts"] >= 1
    assert store.claim_evidence(result["id"])
    assert any(row["relation"] == "conflict" for row in store.claim_evidence(result["id"]))


async def test_freshness_gap_triggers_followup_search(runtime):
    engine, store = runtime
    run = await new_run(engine, store, ["产品 A"])
    plan = {**run["plan"], "subjects": ["产品 A"], "dimensions": ["产品形态"], "as_of": "2026-10-01", "max_evidence_age_days": 30, "require_freshness": True, "max_gap_rounds": 1}
    store.update(run["id"], plan=plan)
    await start(engine, store, run)
    result = await wait_status(store, run["id"], {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    evidence = store.evidence(result["id"])
    assert len(evidence) == 2
    assert any(item["published_at"] is None for item in evidence)
    assert any(item["published_at"] for item in evidence)
    checked = store.query("SELECT * FROM events WHERE run_id=? AND type='coverage.checked'", (result["id"],))
    assert any(event["payload"].get("will_retry") for event in checked)
    assert any(event["payload"].get("freshness_gaps") for event in checked)


async def test_live_reuse_skips_stale_even_if_just_fetched(tmp_path):
    store = Store(tmp_path / "reuse.sqlite")
    project = store.create_project("复用", "")
    parent = store.create_run(project["id"], "父任务", "live", {"subjects": ["A"], "dimensions": ["定价"]})
    store.add_evidence(project["id"], parent["id"], "research:A:0", {
        "title": "A · 定价",
        "url": "https://example.com/old",
        "quote": "2020 年定价 9 美元",
        "locator": "p1",
        "source_type": "web",
        "dimension": "定价",
        "published_at": "2020-01-01",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    plan = {"as_of": "2026-10-01", "max_evidence_age_days": 365, "require_freshness": True}
    parent_items = store.evidence(parent["id"])
    assert parent_items[0]["published_at"] == "2020-01-01T00:00:00+00:00"
    assert can_reuse_evidence(parent_items[0], plan, "live") is False
    store.close()


def test_plan_accepts_freshness_fields():
    plan = ResearchPlan(goal="比较各产品定价并核对时效", subjects=["A"], dimensions=["定价"], as_of="2026-10-01", require_freshness=True, max_evidence_age_days=90)
    assert plan.as_of == "2026-10-01"
    assert plan.require_freshness is True
