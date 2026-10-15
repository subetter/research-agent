import json
import sys
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parents[3] / "evaluations"
sys.path.insert(0, str(EVAL_ROOT))
sys.path.insert(0, str(EVAL_ROOT.parent / "apps" / "api"))

from catalog import TASKS, LIVE_VERDICTS, compile_task
from score import score_artifact, summarize, render_table


def test_catalog_covers_three_families():
    families = {task["family"] for task in TASKS}
    assert 8 <= len(TASKS) <= 12
    assert families == {"competitor", "missing", "conflict"}
    assert sum(task["family"] == "competitor" for task in TASKS) >= 3
    assert sum(task["family"] == "missing" for task in TASKS) >= 3
    assert sum(task["family"] == "conflict" for task in TASKS) >= 3


def test_compile_task_pins_http_and_live_verdicts():
    task = next(item for item in TASKS if item["id"] == "comp-chatgpt-gemini")
    compiled = compile_task(task)
    assert "ChatGPT 产品形态 定价 官方" in compiled["tavily"]
    assert compiled["tavily"]["ChatGPT 产品形态 定价 官方"]["results"][0]["raw_content"]
    assert "plan" in compiled["model"]
    assert compiled["model"]["research-model:ChatGPT:0:0"]["tool_calls"]
    for system in ("workbench", "baseline"):
        for row in compiled["policies"][system]["verify"]:
            assert row["verification"] in LIVE_VERDICTS
    ink = compile_task(next(item for item in TASKS if item["id"] == "miss-inkbot-price"))
    assert all(row["dimension"] != "定价" or row["subject"] != "InkBot" for row in ink["policies"]["workbench"]["verify"])


def test_score_does_not_invent_and_demo_support_is_na():
    demo = score_artifact({
        "task_id": "demo-x",
        "family": "competitor",
        "system": "workbench",
        "repeat": 1,
        "status": "completed",
        "mode": "demo",
        "artifact": {
            "claims": [{"kind": "fact", "evidence_ids": ["ev_1"], "verification": "reference_checked"}],
            "coverage": {"covered": 1, "total": 2},
        },
        "evidence": [{"id": "ev_1"}],
        "usage": [{"kind": "model", "calls": 3, "tokens": 12}, {"kind": "search", "calls": 1, "tokens": 0}],
    })
    assert demo["coverage_rate"] == 0.5
    assert demo["citation_completeness"] == 1.0
    assert demo["citation_support"] is None
    assert "demo" in demo["support_note"]

    empty = score_artifact({"task_id": "empty", "system": "baseline", "repeat": 1, "status": "completed", "mode": "live"})
    assert empty["coverage_rate"] is None
    assert empty["citation_completeness"] is None
    assert empty["citation_support"] == 0.0
    assert empty["model_calls"] is None


def test_summarize_means_all_repeats_and_lists_failures():
    rows = [
        {"system": "workbench", "task_id": "a", "failed": False, "coverage_rate": 1.0, "citation_completeness": 1.0, "citation_support": 1.0, "model_calls": 8, "search_calls": 2, "tokens": 10},
        {"system": "workbench", "task_id": "a", "failed": False, "coverage_rate": 0.5, "citation_completeness": 1.0, "citation_support": 0.5, "model_calls": 8, "search_calls": 2, "tokens": 10},
        {"system": "workbench", "task_id": "b", "failed": True, "coverage_rate": 0.0, "citation_completeness": None, "citation_support": 0.0, "model_calls": 1, "search_calls": 0, "tokens": 2},
    ]
    summary = summarize(rows)
    assert summary["workbench"]["n"] == 3
    assert summary["workbench"]["coverage_rate"] == (1.0 + 0.5 + 0.0) / 3
    assert summary["workbench"]["failed"] == ["b"]
    table = render_table(rows)
    assert "means over all repeats" in table
    assert "failed=b" in table


async def test_offline_eval_live_not_demo_and_no_http(tmp_path, monkeypatch):
    from build_fixtures import write_fixtures
    import run as eval_run

    monkeypatch.setattr(eval_run, "FIXTURES", tmp_path / "fixtures")
    monkeypatch.setattr("build_fixtures.FIXTURES", tmp_path / "fixtures")
    write_fixtures()

    http_hits = []

    async def forbidden_complete(*args, **kwargs):
        http_hits.append("complete")
        raise AssertionError("offline eval must not call the model HTTP client")

    async def forbidden_post(*args, **kwargs):
        http_hits.append("tavily")
        raise AssertionError("offline eval must not call Tavily")

    monkeypatch.setattr("app.providers.complete", forbidden_complete)
    monkeypatch.setattr("httpx.AsyncClient.post", forbidden_post)

    fixtures = eval_run.load_fixture("comp-chatgpt-gemini")
    workbench = await eval_run.run_workbench(fixtures, tmp_path / "w1", 1)
    baseline = await eval_run.run_baseline(fixtures, tmp_path / "b1", 1)

    assert http_hits == []
    assert workbench["mode"] == "live"
    assert baseline["mode"] == "live"
    assert workbench["status"] in {"completed", "partial"}
    assert baseline["status"] in {"completed", "partial"}
    assert not workbench.get("error")
    assert workbench["artifact"]["mode"] == "live"
    assert workbench["artifact"]["coverage"]["covered"] == workbench["artifact"]["coverage"]["total"]
    assert baseline["artifact"]["coverage"]["covered"] == 0
    workbench_score = score_artifact(workbench)
    baseline_score = score_artifact(baseline)
    assert workbench_score["coverage_rate"] == 1.0
    assert workbench_score["citation_support"] == 1.0
    assert baseline_score["coverage_rate"] == 0.0
    assert workbench_score["model_calls"] > baseline_score["model_calls"]
    assert baseline_score["search_calls"] == 1
    for claim in workbench["artifact"]["claims"]:
        assert claim["verification"] != "reference_checked"
