from app.config import Settings
from app.engine import validate_claims
from app.llm import ModelRequestError
from app.providers import Provider, apply_demo_verification, apply_live_verdicts, bind_claim_ids
from app.store import Store


def _evidence(store, project_id, run_id, quote="官方文档写明支持私有化部署。"):
    return store.add_evidence(project_id, run_id, "research:A:0", {
        "title": "官方文档",
        "url": "https://example.com/deploy",
        "quote": quote,
        "locator": "chars:0-20",
        "source_type": "web",
        "dimensions": ["部署方式"],
    })


async def test_supported_claim_stays(tmp_path, monkeypatch):
    store = Store(tmp_path / "verify.sqlite")
    project = store.create_project("核验", "")
    run = store.create_run(project["id"], "研究部署方式是否支持", "live", {})
    ev_id = _evidence(store, project["id"], run["id"])
    provider = Provider(Settings(_env_file=None), store)
    calls = []

    async def complete(*args, **kwargs):
        calls.append(kwargs)
        return {"choices": [{"message": {"role": "assistant", "content": '{"results":[{"subject":"A","dimension":"部署方式","verification":"fully"}]}'}}], "usage": {"total_tokens": 8}}

    async def gate():
        pass

    monkeypatch.setattr("app.providers.complete", complete)
    bundle = {"claims": [{"subject": "A", "dimension": "部署方式", "text": "支持私有化部署。", "kind": "fact", "evidence_ids": [ev_id]}], "summary": ""}
    result = await provider.verify(run, {"subjects": ["A"], "dimensions": ["部署方式"]}, bundle, store.evidence(run["id"]), gate)
    claim = result["claims"][0]
    assert claim["kind"] == "fact"
    assert claim["text"] == "支持私有化部署。"
    assert claim["verification"] == "fully"
    assert claim["evidence_ids"] == [ev_id]
    assert calls
    store.close()


async def test_unsupported_fact_is_downgraded(tmp_path, monkeypatch):
    store = Store(tmp_path / "verify.sqlite")
    project = store.create_project("核验", "")
    run = store.create_run(project["id"], "研究定价", "live", {})
    ev_id = _evidence(store, project["id"], run["id"], "文档只谈部署，没有定价。")
    provider = Provider(Settings(_env_file=None), store)

    async def complete(*args, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": '{"results":[{"subject":"A","dimension":"定价","verification":"unrelated"}]}'}}]}

    async def gate():
        pass

    monkeypatch.setattr("app.providers.complete", complete)
    original = "全年免费。"
    bundle = {"claims": [{"subject": "A", "dimension": "定价", "text": original, "kind": "fact", "evidence_ids": [ev_id]}], "summary": ""}
    result = await provider.verify(run, {"subjects": ["A"], "dimensions": ["定价"]}, bundle, store.evidence(run["id"]), gate)
    claim = result["claims"][0]
    assert claim["kind"] == "unknown"
    assert claim["verification"] == "unrelated"
    assert claim["text"] == original
    store.close()


async def test_verifier_cannot_add_evidence_ids(tmp_path, monkeypatch):
    store = Store(tmp_path / "verify.sqlite")
    project = store.create_project("核验", "")
    run = store.create_run(project["id"], "研究部署方式", "live", {})
    ev_id = _evidence(store, project["id"], run["id"])
    provider = Provider(Settings(_env_file=None), store)
    seen = {}

    async def complete(*args, **kwargs):
        seen["messages"] = args[1]
        return {"choices": [{"message": {"role": "assistant", "content": '{"results":[{"subject":"A","dimension":"部署方式","verification":"fully","evidence_ids":["invented","ev_forged"]}]}'}}]}

    async def gate():
        pass

    monkeypatch.setattr("app.providers.complete", complete)
    bundle = {"claims": [{"subject": "A", "dimension": "部署方式", "text": "支持私有化部署。", "kind": "fact", "evidence_ids": [ev_id, "invented"]}], "summary": ""}
    result = await provider.verify(run, {"subjects": ["A"], "dimensions": ["部署方式"]}, bundle, store.evidence(run["id"]), gate)
    claim = result["claims"][0]
    assert claim["evidence_ids"] == [ev_id]
    assert "invented" not in claim["evidence_ids"]
    published = validate_claims({"subjects": ["A"], "dimensions": ["部署方式"]}, result, store.evidence(run["id"]))
    assert published[0]["evidence_ids"] == [ev_id]
    store.close()


async def test_demo_verify_does_not_call_model(tmp_path, monkeypatch):
    store = Store(tmp_path / "verify.sqlite")
    project = store.create_project("核验", "")
    run = store.create_run(project["id"], "演示核验", "demo", {})
    ev_id = _evidence(store, project["id"], run["id"])
    provider = Provider(Settings(_env_file=None), store)
    calls = []

    async def complete(*args, **kwargs):
        calls.append(1)
        raise AssertionError("demo 核验不应请求模型")

    async def gate():
        pass

    monkeypatch.setattr("app.providers.complete", complete)
    bundle = {"claims": [{"subject": "A", "dimension": "部署方式", "text": "模拟结论。", "kind": "analysis", "evidence_ids": [ev_id]}], "summary": ""}
    result = await provider.verify(run, {"subjects": ["A"], "dimensions": ["部署方式"]}, bundle, store.evidence(run["id"]), gate)
    assert result["claims"][0]["verification"] == "reference_checked"
    assert result["claims"][0]["kind"] == "analysis"
    assert calls == []
    assert store.usage_count(run["id"], "model") == 0
    store.close()


async def test_verify_4xx_degrades_instead_of_failing(tmp_path, monkeypatch):
    store = Store(tmp_path / "verify.sqlite")
    project = store.create_project("核验", "")
    run = store.create_run(project["id"], "研究部署方式", "live", {})
    ev_id = _evidence(store, project["id"], run["id"])
    provider = Provider(Settings(_env_file=None), store)

    async def complete(*args, **kwargs):
        raise ModelRequestError("模型请求参数不兼容，请检查模型名称、思考模式与输出上限：thinking mismatch", status_code=400, body="thinking mismatch")

    async def gate():
        pass

    monkeypatch.setattr("app.providers.complete", complete)
    bundle = {"claims": [{"subject": "A", "dimension": "部署方式", "text": "支持私有化。", "kind": "fact", "evidence_ids": [ev_id]}], "summary": "摘要"}
    result = await provider.verify(run, {"subjects": ["A"], "dimensions": ["部署方式"]}, bundle, store.evidence(run["id"]), gate)
    assert result["claims"][0]["verification"] == "unconfirmed"
    assert result["claims"][0]["kind"] == "unknown"
    events = store.query("SELECT * FROM events WHERE run_id=? AND type='verify.degraded'", (run["id"],))
    assert events
    assert events[0]["payload"]["status_code"] == 400
    store.close()


async def test_verify_chunks_large_claim_batches(tmp_path, monkeypatch):
    store = Store(tmp_path / "verify.sqlite")
    project = store.create_project("核验", "")
    run = store.create_run(project["id"], "研究多格", "live", {})
    ids = [_evidence(store, project["id"], run["id"], f"原文 {index}") for index in range(5)]
    provider = Provider(Settings(_env_file=None), store)
    calls = []

    async def complete(*args, **kwargs):
        calls.append(args[1][1]["content"])
        return {"choices": [{"message": {"role": "assistant", "content": '{"results":[]}'}}], "usage": {"total_tokens": 4}}

    async def gate():
        pass

    monkeypatch.setattr("app.providers.complete", complete)
    claims = [{"subject": "A", "dimension": f"维{index}", "text": "有原文。", "kind": "fact", "evidence_ids": [ids[index]]} for index in range(5)]
    await provider.verify(run, {"subjects": ["A"], "dimensions": [f"维{index}" for index in range(5)]}, {"claims": claims, "summary": ""}, store.evidence(run["id"]), gate)
    assert len(calls) == 2
    store.close()


def test_bind_claim_ids_never_adds():
    bound = bind_claim_ids({"evidence_ids": ["a", "missing", "a"]}, {"a"})
    assert bound["evidence_ids"] == ["a"]
    live = apply_live_verdicts(
        [{"subject": "A", "dimension": "定价", "text": "免费", "kind": "fact", "evidence_ids": ["a"]}],
        {("A", "定价"): "contradicted"},
        {"a"},
    )
    assert live[0]["kind"] == "unknown"
    assert live[0]["text"] == "免费"
    demo = apply_demo_verification(
        [{"subject": "A", "dimension": "定价", "text": "模拟", "kind": "analysis", "evidence_ids": ["a"]}],
        {"a"},
    )
    assert demo[0]["verification"] == "reference_checked"
