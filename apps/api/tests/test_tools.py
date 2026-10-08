import pytest
from app.config import Settings
from app.providers import Provider
from app.store import Store


@pytest.mark.parametrize("raw,expected", [("", 0), ("官网原文段落，明确说明支持团队部署。", 1)])
async def test_tool_loop_requires_raw_source(tmp_path, raw, expected):
    store = Store(tmp_path / "tools.sqlite")
    p = store.create_project("工具测试", "")
    run = store.create_run(p["id"], "研究产品部署方式", "live", {})
    provider = Provider(Settings(_env_file=None), store)
    answers = iter([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search_web", "arguments": '{"query":"A 部署 官方"}'}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read_source", "arguments": '{"url":"https://example.com/product"}'}}]},
        {"role": "assistant", "content": "调查完成"},
    ])
    async def fake_chat(*args, **kwargs):
        return next(answers)
    async def fake_search(*args, **kwargs):
        return {"results": [{"title": "官方文档", "url": "https://example.com/product", "snippet": "搜索摘要不能直接作为证据", "raw_content": raw}]}
    async def gate():
        pass
    provider.chat = fake_chat
    provider.search = fake_search
    result = await provider.live_research(run, {"subjects": ["A"], "dimensions": ["部署方式"], "max_search_calls": 3}, "A", 0, gate)
    assert len(result["evidence_ids"]) == expected
    evidence = store.evidence(run["id"])
    if expected:
        assert evidence[0]["quote"] == raw
        assert evidence[0]["locator"].startswith("chars:")
    assert not any("搜索摘要" in e["quote"] for e in evidence)
    store.close()
