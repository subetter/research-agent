import json

from app.config import Settings
from app.providers import Provider
from app.store import Store, content_hash
from app.synthesis import SYNTHESIS_SYSTEM, build_synthesis_context, model_payload
from app.tracing import Tracing
from tests.test_workbench import new_run, start, wait_status

pytest_plugins = ("tests.test_workbench",)


SUBJECTS = [f"对象{index}" for index in range(1, 7)]
DIMENSIONS = ["产品形态", "定价", "部署方式", "目标用户"]


def evidence_item(subject, dimension, index, quote, **extra):
    digest = extra.pop("content_hash", content_hash(quote))
    return {
        "id": extra.pop("id", f"ev_{subject}_{dimension}_{index}"),
        "task_key": f"research:{subject}:0",
        "title": extra.pop("title", f"{subject} {dimension} 来源 {index}"),
        "url": extra.pop("url", f"https://example.com/{subject}/{dimension}/{index}"),
        "canonical_url": extra.pop("canonical_url", f"https://example.com/{subject}/{dimension}/{index}"),
        "quote": quote,
        "dimensions": extra.pop("dimensions", [dimension]),
        "content_hash": digest,
        "citation_role": extra.pop("citation_role", "fact"),
        **extra,
    }


def grid_evidence(quotes_per_cell=3, quote=""):
    items = []
    for subject in SUBJECTS:
        for dimension in DIMENSIONS:
            for index in range(quotes_per_cell):
                text = quote or f"{subject} 关于{dimension}的原文段落 {index}。" + ("详述。" * 80)
                items.append(evidence_item(subject, dimension, index, text))
    return items


def test_six_by_four_all_cells_reach_context():
    plan = {"subjects": SUBJECTS, "dimensions": DIMENSIONS}
    evidence = grid_evidence(quotes_per_cell=3)
    assert len(evidence) == 72
    selection = build_synthesis_context(plan, evidence, budget=120000, quotes_per_cell=2)
    pairs = {(cell["subject"], cell["dimension"]) for cell in selection["cells"]}
    assert pairs == {(subject, dimension) for subject in SUBJECTS for dimension in DIMENSIONS}
    assert all(cell["status"] == "covered" and cell["quote_count"] == 2 for cell in selection["cells"])
    assert len(selection["included_evidence_ids"]) == 48
    payload = model_payload(plan, selection)
    assert len(payload["cells"]) == 24
    assert all(cell["quotes"] for cell in payload["cells"])


def test_budget_squeeze_keeps_every_cell():
    plan = {"subjects": SUBJECTS, "dimensions": DIMENSIONS}
    evidence = grid_evidence(quotes_per_cell=3)
    selection = build_synthesis_context(plan, evidence, budget=4000, quotes_per_cell=2)
    assert len(selection["cells"]) == 24
    assert {cell["status"] for cell in selection["cells"]} == {"covered"}
    assert selection["quotes_per_cell"] == 1
    assert selection["degraded"] is True
    assert all(cell["quote_count"] == 1 for cell in selection["cells"])
    assert all(cell["quotes"][0]["id"] for cell in selection["cells"])


def test_missing_cells_are_explicit():
    plan = {"subjects": ["A", "B"], "dimensions": ["定价", "部署方式"]}
    evidence = [
        evidence_item("A", "定价", 0, "A 的定价页写明按席位收费。"),
    ]
    selection = build_synthesis_context(plan, evidence, budget=8000, quotes_per_cell=2)
    by_pair = {(cell["subject"], cell["dimension"]): cell for cell in selection["cells"]}
    assert by_pair[("A", "定价")]["status"] == "covered"
    assert by_pair[("A", "定价")]["quote_count"] == 1
    assert by_pair[("A", "部署方式")]["status"] == "missing"
    assert by_pair[("B", "定价")]["status"] == "missing"
    assert by_pair[("B", "部署方式")]["status"] == "missing"
    assert by_pair[("A", "部署方式")]["quotes"] == []
    payload = model_payload(plan, selection)
    missing = [cell for cell in payload["cells"] if cell["status"] == "missing"]
    assert len(missing) == 3
    assert "禁止编造" in payload["notes"]["missing_cells"]
    assert "只支持同一主张一次" in payload["notes"]["reprints"]


def test_reprints_are_deduped_within_a_cell():
    plan = {"subjects": ["A"], "dimensions": ["定价"]}
    original = "官方定价为每席位每月 20 美元，按年付费可打折。"
    evidence = [
        evidence_item("A", "定价", 0, original, id="ev_orig", canonical_url="https://vendor.example/pricing"),
        evidence_item("A", "定价", 1, original, id="ev_reprint", url="https://mirror.example/pricing", canonical_url="https://mirror.example/pricing"),
        evidence_item("A", "定价", 2, "另一段原文：免费档不开放团队席位。", id="ev_other"),
    ]
    assert evidence[0]["content_hash"] == evidence[1]["content_hash"]
    selection = build_synthesis_context(plan, evidence, budget=8000, quotes_per_cell=2)
    ids = selection["cells"][0]["evidence_ids"]
    assert "ev_other" in ids
    assert ("ev_orig" in ids) != ("ev_reprint" in ids)
    assert len(ids) == 2
    assert len(selection["included_evidence_ids"]) == 2


def test_store_keeps_full_quote_when_context_truncates(tmp_path):
    store = Store(tmp_path / "full.sqlite")
    project = store.create_project("完整原文", "")
    run = store.create_run(project["id"], "保留完整原文", "live", {})
    long_quote = "部署方式支持私有化。" + ("补充说明。" * 400)
    evidence_id = store.add_evidence(project["id"], run["id"], "research:A:0", {
        "title": "A 部署文档",
        "url": "https://example.com/deploy",
        "quote": long_quote,
        "locator": "chars:0-2000",
        "source_type": "web",
        "dimensions": ["部署方式"],
    })
    stored = next(item for item in store.evidence(run["id"]) if item["id"] == evidence_id)
    assert stored["quote"] == long_quote
    assert len(stored["quote"]) > 1600
    selection = build_synthesis_context(
        {"subjects": ["A"], "dimensions": ["部署方式"]},
        store.evidence(run["id"]),
        budget=500,
        quotes_per_cell=2,
    )
    sent = selection["cells"][0]["quotes"][0]["quote"]
    assert len(sent) < len(long_quote)
    assert store.evidence(run["id"])[0]["quote"] == long_quote
    store.close()


async def test_live_synthesize_sends_all_cells_and_records_ids(tmp_path, monkeypatch):
    store = Store(tmp_path / "live.sqlite")
    settings = Settings(_env_file=None, synthesis_context_budget=8000, synthesis_quotes_per_cell=2)
    provider = Provider(settings, store)
    captured = []
    updates = []

    class FakeTrace:
        def update(self, **kwargs):
            updates.append(kwargs)

    class FakeClient:
        def trace(self, **kwargs):
            return FakeTrace()

    tracing = Tracing(enabled=True, client=FakeClient(), host="http://localhost:3001")
    monkeypatch.setattr("app.providers.get_tracing", lambda: tracing)

    async def fake_chat(run_id, messages, key, tools=None):
        captured.append({"key": key, "system": messages[0]["content"], "user": json.loads(messages[1]["content"])})
        cells = json.loads(messages[1]["content"])["cells"]
        claims = [{"subject": cell["subject"], "dimension": cell["dimension"], "text": "待确认" if cell["status"] == "missing" else "有证据", "evidence_ids": [item["id"] for item in cell["quotes"]], "kind": "unknown" if cell["status"] == "missing" else "fact"} for cell in cells]
        return {"content": json.dumps({"claims": claims, "summary": "ok"}, ensure_ascii=False)}

    provider.chat = fake_chat
    project = store.create_project("综合", "")
    run = store.create_run(project["id"], "比较六个对象", "live", {})
    token = tracing.start(run["id"], name="research-run")
    missing_prefix = f"ev_{SUBJECTS[-1]}_{DIMENSIONS[-1]}_"
    evidence = [item for item in grid_evidence(quotes_per_cell=3) if not item["id"].startswith(missing_prefix)]
    plan = {"subjects": SUBJECTS, "dimensions": DIMENSIONS, "goal": "比较"}

    async def gate():
        return None

    bundle = await provider.synthesize(run, plan, evidence, gate)
    tracing.reset(token)
    assert captured[0]["key"] == "synthesize"
    assert "只算一次支持" in captured[0]["system"]
    assert "status=missing" in captured[0]["system"]
    assert SYNTHESIS_SYSTEM[:12] in captured[0]["system"]
    cells = captured[0]["user"]["cells"]
    assert len(cells) == 24
    last = next(cell for cell in cells if cell["subject"] == SUBJECTS[-1] and cell["dimension"] == DIMENSIONS[-1])
    assert last["status"] == "missing"
    assert last["quotes"] == []
    missing_claim = next(item for item in bundle["claims"] if item["subject"] == SUBJECTS[-1] and item["dimension"] == DIMENSIONS[-1])
    assert missing_claim["kind"] == "unknown"
    events = store.query("SELECT * FROM events WHERE run_id=? AND type='synthesis.context'", (run["id"],))
    assert len(events) == 1
    payload = events[0]["payload"]
    assert len(payload["cells"]) == 24
    assert last["status"] == payload["cells"][-1]["status"]
    assert payload["included_evidence_ids"]
    assert updates
    assert updates[0]["metadata"]["synthesis"]["included_evidence_ids"] == payload["included_evidence_ids"]
    store.close()


async def test_demo_run_exposes_per_cell_quote_counts(runtime):
    engine, store = runtime
    run = await new_run(engine, store, ["产品 A", "产品 B"])
    await start(engine, store, run)
    result = await wait_status(store, run["id"], {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    events = store.query("SELECT * FROM events WHERE run_id=? AND type='synthesis.context'", (run["id"],))
    assert events
    payload = events[0]["payload"]
    assert len(payload["included_evidence_ids"]) == 4
    coverage = result["artifact"]["coverage"]
    assert all(cell["context_quote_count"] == 1 for cell in coverage["cells"])
    assert coverage["context_included_ids"] == payload["included_evidence_ids"]
