"""Regression for the five restack bugs: budget retry, free/paid, Chinese FTS, gap briefs, async retrieval."""

import asyncio
import json
import threading

import pytest

from app.config import Settings
from app.conflicts import extract_signals, signals_conflict
from app.documents import ingest_text
from app.embeddings import fts_index_text, tokenize
from app.engine import merge_gap_targets, research_targets
from app.providers import Provider
from app.retrieval import asearch_project_documents, search_project_documents
from app.store import Store


async def test_failed_llm_unknown_status_can_retry_same_key(tmp_path, monkeypatch):
    store = Store(tmp_path / "retry.sqlite")
    project = store.create_project("重试", "")
    run = store.create_run(project["id"], "网络失败后重试同一操作", "live", {})
    provider = Provider(Settings(_env_file=None, max_model_calls=3, report_reserved_calls=1), store)
    attempts = {"n": 0}

    async def flaky(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("network down")
        return {"choices": [{"message": {"role": "assistant", "content": '{"ok":true}'}}], "usage": {"total_tokens": 4}}

    monkeypatch.setattr("app.providers.complete", flaky)
    with pytest.raises(ConnectionError):
        await provider.chat(run["id"], [{"role": "user", "content": "x"}], "research:A:0")
    row = store.query("SELECT status FROM usage WHERE run_id=? AND key=?", (run["id"], "research:A:0"))[0]
    assert row["status"] == "unknown"
    result = await provider.chat(run["id"], [{"role": "user", "content": "x"}], "research:A:0")
    assert result["content"] == '{"ok":true}'
    assert attempts["n"] == 2
    assert store.usage_count(run["id"], "model") == 1
    settled = store.query("SELECT status FROM usage WHERE run_id=? AND key=?", (run["id"], "research:A:0"))[0]
    assert settled["status"] == "succeeded"
    store.close()


def test_free_vs_paid_conflict_both_directions():
    free = extract_signals("个人版免费使用")
    paid = extract_signals("个人版开始收费，订阅制")
    assert free["free"] and not free["paid"]
    assert paid["paid"] and not paid["free"]
    assert signals_conflict(free, paid)
    assert signals_conflict(paid, free)
    both = extract_signals("有免费额度，超出后付费订阅")
    assert both["free"] and both["paid"]
    assert not signals_conflict(both, paid)


def test_chinese_fts_uses_ngrams_and_keeps_english(tmp_path):
    settings = Settings(_env_file=None)
    store = Store(tmp_path / "fts.sqlite")
    project = store.create_project("检索", "")
    ingest_text(store, settings, project["id"], "记忆.md", "智能体记忆系统把短期上下文写入长期存储。")
    ingest_text(store, settings, project["id"], "pricing.md", "InkBot pricing is twenty dollars per month.")
    tokens = tokenize("智能体记忆系统")
    assert "智能" in tokens and "记忆" in tokens and "记忆系" in tokens
    indexed = fts_index_text("智能体记忆系统")
    assert " 记忆 " in f" {indexed} "
    zh = search_project_documents(store, settings, project["id"], "记忆系统")
    assert zh and any("智能体记忆" in item["quote"] for item in zh)
    en = search_project_documents(store, settings, project["id"], "InkBot pricing")
    assert en and any("InkBot" in item["quote"] for item in en)
    store.close()


def test_gap_followup_passes_concrete_missing_conflict_freshness():
    plan = {"subjects": ["A"], "dimensions": ["定价", "部署"], "max_gap_rounds": 1}
    coverage_gaps = [{"subject": "A", "dimension": "部署"}]
    assessment = {
        "uncovered": [{"subject": "A", "dimension": "部署", "reason": "uncovered_dimension"}],
        "conflicts": [{"id": "conflict:A:定价", "subject": "A", "dimension": "定价", "positions": []}],
        "freshness_gaps": [{"subject": "A", "dimension": "定价", "freshness": "stale", "evidence_ids": ["e1"]}],
    }
    structured = merge_gap_targets(plan, coverage_gaps, assessment)
    targets = research_targets(plan, structured)
    assert len(targets) == 1
    subject, dimensions, brief = targets[0]
    assert subject == "A"
    assert set(dimensions) >= {"定价", "部署"}
    assert set(brief["reasons"]) >= {"uncovered", "conflict", "freshness"}
    assert brief["conflicts"] and brief["freshness_gaps"]


async def test_live_research_prompt_includes_gap_brief(tmp_path):
    store = Store(tmp_path / "gap-prompt.sqlite")
    run = store.create_run(store.create_project("缺口提示", "")["id"], "比较定价", "live", {})
    provider = Provider(Settings(_env_file=None), store)
    captured = []

    async def fake_chat(run_id, messages, key, tools=None):
        captured.append(messages)
        return {"role": "assistant", "content": "调查完成"}

    async def gate():
        pass

    provider.chat = fake_chat
    brief = {
        "subject": "A",
        "dimensions": ["定价"],
        "reasons": ["conflict", "freshness"],
        "conflicts": [{"id": "conflict:A:定价", "subject": "A", "dimension": "定价"}],
        "freshness_gaps": [{"subject": "A", "dimension": "定价", "freshness": "stale"}],
    }
    await provider.live_research(run, {"subjects": ["A"], "dimensions": ["定价"], "max_search_calls": 2}, "A", 1, gate, ["定价"], brief)
    assert captured
    system, user = captured[0]
    payload = json.loads(user["content"])
    assert payload["gap"]["reasons"] == ["conflict", "freshness"]
    assert payload["gap"]["dimensions"] == ["定价"]
    assert "补充轮" in system["content"]
    assert "gap" in system["content"] or "缺口" in system["content"]
    store.close()


async def test_project_search_runs_off_event_loop(tmp_path, monkeypatch):
    settings = Settings(_env_file=None)
    store = Store(tmp_path / "async-search.sqlite")
    project = store.create_project("异步", "")
    ingest_text(store, settings, project["id"], "note.md", "混合检索不应阻塞事件循环。")
    seen = {}

    def wrapped(*args, **kwargs):
        seen["ident"] = threading.get_ident()
        return search_project_documents(*args, **kwargs)

    monkeypatch.setattr("app.retrieval.search_project_documents", wrapped)
    hits = await asearch_project_documents(store, settings, project["id"], "事件循环")
    assert hits
    assert seen["ident"] != threading.get_ident()
    store.close()


def test_asearch_uses_to_thread():
    source = asyncio.to_thread
    assert asearch_project_documents.__doc__
    text = asearch_project_documents.__code__.co_names
    assert "to_thread" in text or source is asyncio.to_thread
    import inspect
    body = inspect.getsource(asearch_project_documents)
    assert "asyncio.to_thread" in body
