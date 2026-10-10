import sys
from pathlib import Path

import pytest
from app.config import Settings

EVAL_ROOT = Path(__file__).resolve().parents[3] / "evaluations"
sys.path.insert(0, str(EVAL_ROOT))

from agreement import agreement_from_rows
from live_eval import (
    SAMPLE_COLUMNS,
    SMOKE_TASKS,
    LiveEvalConfigError,
    build_sample_rows,
    estimate_cost,
    live_out_dir,
    price_table,
    require_live_keys,
    resolve_task_ids,
    summarize_cost,
    write_samples_csv,
)


def empty_settings(**overrides):
    payload = {
        "llm_api_key": "",
        "llm_model": "",
        "tavily_api_key": "",
    }
    payload.update(overrides)
    return Settings(_env_file=None, **payload)


def test_live_refuses_without_keys():
    with pytest.raises(LiveEvalConfigError) as exc:
        require_live_keys(empty_settings())
    message = str(exc.value)
    assert "LLM_API_KEY" in message
    assert "TAVILY_API_KEY" in message
    assert "不会回落到演示" in message


def test_live_refuses_partial_keys_without_leaking_values():
    with pytest.raises(LiveEvalConfigError) as exc:
        require_live_keys(empty_settings(llm_api_key="sk-secret-live", tavily_api_key="tvly-secret"))
    message = str(exc.value)
    assert "LLM_MODEL" in message
    assert "sk-secret-live" not in message
    assert "tvly-secret" not in message


def test_subset_defaults_to_three_family_smoke():
    assert resolve_task_ids(None, live=True) == list(SMOKE_TASKS)
    assert resolve_task_ids(None, live=False)[:3]
    from catalog import TASKS
    families = {task["family"] for task in TASKS if task["id"] in SMOKE_TASKS}
    assert families == {"competitor", "missing", "conflict"}
    assert resolve_task_ids(["conf-date"], live=True) == ["conf-date"]
    with pytest.raises(LiveEvalConfigError):
        resolve_task_ids(["not-a-task"], live=True)


def test_cost_math_uses_price_table():
    settings = empty_settings(
        eval_price_version="test-table",
        eval_deepseek_prompt_usd_per_1m=1.0,
        eval_deepseek_completion_usd_per_1m=2.0,
        eval_deepseek_reasoning_usd_per_1m=3.0,
        eval_tavily_search_usd=0.5,
    )
    prices = price_table(settings)
    assert prices["version"] == "test-table"
    assert "不是账单" in prices["disclaimer"]
    split = estimate_cost(1_000_000, 500_000, 100_000, 2, prices)
    assert split["estimated_usd"] == pytest.approx(1.0 + 1.0 + 1.0)
    unknown = estimate_cost(0, 0, 0, 1, prices, total_tokens=1_000_000)
    assert unknown["estimated_usd"] == pytest.approx(2.0 + 0.5)
    reasoning_only = estimate_cost(0, 0, 1_000_000, 0, prices)
    assert reasoning_only["estimated_usd"] == pytest.approx(3.0)


def test_sampling_sheet_columns_and_empty_human_label(tmp_path):
    payloads = [
        {
            "task_id": "comp-chatgpt-gemini",
            "system": "workbench",
            "repeat": 1,
            "evidence": [{"id": "ev_1", "quote": "ChatGPT Plus 每月 20 美元", "url": "https://example.com/a"}],
            "artifact": {
                "claims": [
                    {"subject": "ChatGPT", "dimension": "定价", "text": "Plus 每月 20 美元", "evidence_ids": ["ev_1"], "verification": "fully"},
                    {"subject": "Gemini", "dimension": "定价", "text": "待确认", "evidence_ids": [], "verification": "unconfirmed"},
                ]
            },
        }
    ]
    rows = build_sample_rows(payloads, limit=30)
    assert [row.keys() for row in rows] == [dict.fromkeys(SAMPLE_COLUMNS).keys()]
    assert list(rows[0]) == list(SAMPLE_COLUMNS)
    assert len(rows) == 1
    assert rows[0]["cited_quote"] == "ChatGPT Plus 每月 20 美元"
    assert rows[0]["url"] == "https://example.com/a"
    assert rows[0]["verifier_label"] == "fully"
    assert rows[0]["human_label"] == ""
    path = write_samples_csv(tmp_path / "samples.csv", rows)
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(SAMPLE_COLUMNS)
    assert "human_label" in text.splitlines()[0]
    labeled = [{**rows[0], "human_label": "fully"}]
    result = agreement_from_rows(labeled)
    assert result["n"] == 1 and result["agree"] == 1 and result["rate"] == 1.0
    empty = agreement_from_rows(rows)
    assert empty["n"] == 0 and empty["rate"] is None


def test_projection_scales_three_tasks_to_ten():
    prices = price_table(empty_settings())
    payloads = []
    for task_id in SMOKE_TASKS:
        payloads.append({"task_id": task_id, "system": "workbench", "cost": {"estimated_usd": 0.2}})
        payloads.append({"task_id": task_id, "system": "baseline", "cost": {"estimated_usd": 0.1}})
    summary = summarize_cost(payloads, prices, catalog_size=10)
    assert summary["n_tasks"] == 3
    assert summary["batch_estimated_usd"] == pytest.approx(0.9)
    assert summary["projected_10_tasks_usd"] == pytest.approx(3.0)
    assert summary["price_version"]
    assert live_out_dir(Path("/tmp"), "20261010T010203Z") == Path("/tmp/results/live-20261010T010203Z")


async def test_preflight_aborts_on_mocked_400(monkeypatch):
    from app.llm import ModelRequestError
    from live_eval import preflight_live

    seen = []

    async def complete(settings, messages, tools=None, json_output=False):
        seen.append((bool(tools), json_output))
        if json_output and not tools and len(seen) >= 3:
            raise ModelRequestError("模型请求参数不兼容", status_code=400, body="bad verify")
        return {"choices": [{"message": {"role": "assistant", "content": "{}"}}], "usage": {"total_tokens": 1}}

    monkeypatch.setattr("app.llm.complete", complete)
    with pytest.raises(LiveEvalConfigError) as exc:
        await preflight_live(empty_settings(llm_api_key="k", llm_model="m", tavily_api_key="t"))
    assert "预检失败" in str(exc.value)
    assert "400" in str(exc.value)
    assert "k" not in str(exc.value)


async def test_token_ceiling_stops_research_not_report(tmp_path, monkeypatch):
    from app.providers import BudgetExceeded, Provider
    from app.store import Store

    async def complete(*args, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "{}"}}], "usage": {"total_tokens": 1}}

    monkeypatch.setattr("app.providers.complete", complete)
    store = Store(tmp_path / "ceil.sqlite")
    project = store.create_project("顶", "")
    run = store.create_run(project["id"], "token 顶", "live", {})
    store.reserve(run["id"], "research:0", "model", 80)
    store.settle(run["id"], "research:0", tokens=90000)
    provider = Provider(Settings(_env_file=None, eval_max_tokens_per_task=80000), store)
    with pytest.raises(BudgetExceeded) as exc:
        await provider.chat(run["id"], [{"role": "user", "content": "x"}], "research:1")
    assert "80000" in str(exc.value)
    await provider.chat(run["id"], [{"role": "user", "content": "x"}], "verify")
    store.close()


async def test_plan_keeps_requested_scope(tmp_path, monkeypatch):
    import json
    from app.providers import Provider
    from app.store import Store

    store = Store(tmp_path / "plan.sqlite")
    project = store.create_project("计划", "")
    run = store.create_run(project["id"], "比较定价", "live", {})
    provider = Provider(Settings(_env_file=None, llm_api_key="k", llm_model="m", tavily_api_key="t"), store)

    async def complete(*args, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": json.dumps({
            "goal": "改写后的目标文本",
            "subjects": ["被改掉的对象"],
            "dimensions": ["定价口径A", "定价口径B"],
            "questions": ["问一句"],
        }, ensure_ascii=False)}}], "usage": {"total_tokens": 6}}

    monkeypatch.setattr("app.providers.complete", complete)
    plan = await provider.plan(run, {"question": "比较 ChatGPT 定价", "subjects": ["ChatGPT"], "dimensions": ["定价"]})
    assert plan["subjects"] == ["ChatGPT"]
    assert plan["dimensions"] == ["定价"]
    store.close()
