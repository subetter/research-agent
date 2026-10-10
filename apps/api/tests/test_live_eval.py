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
