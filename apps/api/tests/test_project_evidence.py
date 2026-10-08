from app.config import Settings
from app.engine import validate_claims
from app.providers import Provider
from app.store import Store


async def test_search_project_does_not_store_keyword_hits(tmp_path):
    store = Store(tmp_path / "docs.sqlite")
    project = store.create_project("资料", "")
    store.add_document(project["id"], "notes.md", "产品形态：这是内部备忘，写了浏览器工作台。\n\n定价：备忘录写每月 9 美元。")
    run = store.create_run(project["id"], "研究产品形态", "live", {})
    provider = Provider(Settings(_env_file=None), store)
    answers = iter([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search_project", "arguments": '{"query":"产品形态"}'}}]},
        {"role": "assistant", "content": "先看候选，先不入证"},
    ])

    async def fake_chat(*args, **kwargs):
        return next(answers)

    async def gate():
        pass

    provider.chat = fake_chat
    result = await provider.live_research(run, {"subjects": ["内部产品"], "dimensions": ["产品形态"], "max_search_calls": 3}, "内部产品", 0, gate)
    assert result["evidence_ids"] == []
    assert store.evidence(run["id"]) == []
    started = store.query("SELECT * FROM events WHERE run_id=? AND type='tool.started' AND json_extract(payload,'$.tool')='search_project'", (run["id"],))
    assert started
    assert started[0]["payload"]["target"] == "产品形态"
    store.close()


async def test_cite_project_stores_only_selected_passage(tmp_path):
    store = Store(tmp_path / "docs.sqlite")
    project = store.create_project("资料", "")
    store.add_document(project["id"], "notes.md", "产品形态：这是内部备忘，写了浏览器工作台。")
    run = store.create_run(project["id"], "研究产品形态", "live", {})
    provider = Provider(Settings(_env_file=None), store)
    candidate = {}

    async def fake_chat(run_id, messages, key, tools=None):
        if key.endswith(":0"):
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search_project", "arguments": '{"query":"产品形态"}'}}]}
        if key.endswith(":1"):
            tool = next(item for item in reversed(messages) if item.get("role") == "tool")
            import json
            body = json.loads(tool["content"])
            candidate["id"] = body["candidates"][0]["id"]
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "cite_project", "arguments": json.dumps({"candidate_id": candidate["id"]})}}]}
        return {"role": "assistant", "content": "已选用"}

    async def gate():
        pass

    provider.chat = fake_chat
    result = await provider.live_research(run, {"subjects": ["内部产品"], "dimensions": ["产品形态"], "max_search_calls": 3}, "内部产品", 0, gate)
    assert len(result["evidence_ids"]) == 1
    evidence = store.evidence(run["id"])
    assert evidence[0]["source_type"] == "document"
    assert evidence[0]["citation_role"] == "analysis"
    assert "浏览器工作台" in evidence[0]["quote"]
    store.close()


def test_uncitable_document_cannot_stay_as_fact(tmp_path):
    store = Store(tmp_path / "docs.sqlite")
    project = store.create_project("资料", "")
    run = store.create_run(project["id"], "研究产品形态", "demo", {})
    analysis_id = store.add_evidence(project["id"], run["id"], "research:内部产品:0", {
        "title": "notes.md",
        "url": "project://doc",
        "quote": "产品形态：内部备忘写了浏览器工作台。",
        "locator": "paragraph:1",
        "source_type": "document",
        "dimensions": ["产品形态"],
        "citable": False,
    })
    fact_id = store.add_evidence(project["id"], run["id"], "research:内部产品:0", {
        "title": "官网",
        "url": "https://example.com/form",
        "quote": "产品形态：官方写明是浏览器工作台。",
        "locator": "chars:0-20",
        "source_type": "web",
        "dimensions": ["产品形态"],
    })
    plan = {"subjects": ["内部产品"], "dimensions": ["产品形态"]}
    only_doc = validate_claims(plan, {"claims": [{"subject": "内部产品", "dimension": "产品形态", "text": "它是浏览器工作台。", "kind": "fact", "evidence_ids": [analysis_id]}]}, store.evidence(run["id"]))
    assert only_doc[0]["kind"] == "analysis"
    assert only_doc[0]["text"] == "它是浏览器工作台。"
    with_web = validate_claims(plan, {"claims": [{"subject": "内部产品", "dimension": "产品形态", "text": "它是浏览器工作台。", "kind": "fact", "evidence_ids": [fact_id]}]}, store.evidence(run["id"]))
    assert with_web[0]["kind"] == "fact"
    store.close()


def test_citable_document_can_support_fact(tmp_path):
    store = Store(tmp_path / "docs.sqlite")
    project = store.create_project("资料", "")
    document = store.add_document(project["id"], "official.md", "产品形态：用户标记为可引用。", citable=True)
    assert document["citable"] is True
    run = store.create_run(project["id"], "研究产品形态", "demo", {})
    evidence_id = store.add_evidence(project["id"], run["id"], "research:内部产品:0", {
        "title": "official.md",
        "url": f"project://{document['id']}",
        "quote": "产品形态：用户标记为可引用。",
        "locator": "paragraph:1",
        "source_type": "document",
        "dimensions": ["产品形态"],
        "citable": True,
    })
    claims = validate_claims(
        {"subjects": ["内部产品"], "dimensions": ["产品形态"]},
        {"claims": [{"subject": "内部产品", "dimension": "产品形态", "text": "用户标记为可引用。", "kind": "fact", "evidence_ids": [evidence_id]}]},
        store.evidence(run["id"]),
    )
    assert claims[0]["kind"] == "fact"
    store.close()
