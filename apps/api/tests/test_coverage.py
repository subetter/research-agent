import sqlite3

from app.engine import coverage_matrix
from app.providers import extract_dimension_passages
from app.store import Store, canonical_url


def test_gap_check_is_dimension_level():
    plan = {"subjects": ["A", "B"], "dimensions": ["形态", "流程"]}
    evidence = [{"id": "ev1", "task_key": "research:A:0", "title": "A 官网", "dimensions": ["形态"]}]
    coverage = coverage_matrix(plan, evidence)
    assert coverage["covered"] == 1
    assert coverage["total"] == 4
    pairs = {(item["subject"], item["dimension"]) for item in coverage["gaps"]}
    assert pairs == {("A", "流程"), ("B", "形态"), ("B", "流程")}
    assert ("A", "形态") not in pairs


def test_title_fallback_covers_legacy_demo_evidence():
    plan = {"subjects": ["产品 A"], "dimensions": ["产品形态", "研究流程"]}
    evidence = [{
        "id": "ev_legacy",
        "task_key": "research:产品 A:0",
        "title": "产品 A · 产品形态 · 模拟来源",
        "dimensions": [],
    }]
    coverage = coverage_matrix(plan, evidence)
    assert coverage["covered"] == 1
    assert coverage["gaps"] == [{"subject": "产品 A", "dimension": "研究流程"}]


def test_evidence_dedupes_canonical_url_and_same_text(tmp_path):
    store = Store(tmp_path / "dedupe.sqlite")
    project = store.create_project("去重", "")
    run = store.create_run(project["id"], "比较去重行为", "demo", {})
    first = store.add_evidence(project["id"], run["id"], "research:A:0", {
        "title": "官方说明",
        "url": "https://Example.com/a/",
        "quote": "同一段原文，支持定价。",
        "locator": "chars:0-14",
        "source_type": "web",
        "dimensions": ["定价"],
    })
    reprint = store.add_evidence(project["id"], run["id"], "research:A:0", {
        "title": "转载页",
        "url": "https://mirror.example/a?utm=1",
        "quote": "同一段原文，支持定价。",
        "locator": "chars:80-200",
        "source_type": "web",
        "dimensions": ["功能"],
    })
    same_url = store.add_evidence(project["id"], run["id"], "research:A:1", {
        "title": "官方说明副本",
        "url": "https://example.com/a",
        "quote": "同一段原文，支持定价。",
        "locator": "chars:400-500",
        "source_type": "web",
        "dimensions": ["定价"],
    })
    other = store.add_evidence(project["id"], run["id"], "research:A:0", {
        "title": "另一段",
        "url": "https://example.com/a",
        "quote": "另一段原文，说明部署方式。",
        "locator": "chars:20-40",
        "source_type": "web",
        "dimensions": ["部署"],
    })
    assert first == reprint == same_url
    assert other != first
    rows = store.evidence(run["id"])
    assert len(rows) == 2
    kept = next(item for item in rows if item["id"] == first)
    assert set(kept["dimensions"]) == {"定价", "功能"}
    assert kept["canonical_url"] == canonical_url("https://Example.com/a/")
    store.close()


def test_old_schema_migration_adds_evidence_columns(tmp_path):
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, background TEXT, created_at TEXT);
        CREATE TABLE runs (
          id TEXT PRIMARY KEY, project_id TEXT, question TEXT, status TEXT,
          revision INTEGER DEFAULT 0, mode TEXT, plan TEXT, parent_run_id TEXT,
          artifact TEXT, error TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE events (
          run_id TEXT, seq INTEGER, type TEXT, payload TEXT, created_at TEXT,
          PRIMARY KEY(run_id, seq));
        CREATE TABLE evidence (
          id TEXT PRIMARY KEY, project_id TEXT, run_id TEXT,
          task_key TEXT, title TEXT, url TEXT, quote TEXT, locator TEXT,
          source_type TEXT, fetched_at TEXT,
          UNIQUE(run_id, task_key, url, locator));
        CREATE TABLE operations (run_id TEXT, key TEXT, result TEXT, PRIMARY KEY(run_id, key));
        CREATE TABLE usage (
          run_id TEXT, key TEXT, kind TEXT, status TEXT, tokens INTEGER DEFAULT 0,
          PRIMARY KEY(run_id, key));
        CREATE TABLE documents (
          id TEXT PRIMARY KEY, project_id TEXT, name TEXT, content TEXT, created_at TEXT);
    """)
    conn.execute(
        "INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("ev_old", "project_old", "run_old", "research:产品A:0", "产品A · 产品形态 · 模拟来源",
         "https://Example.com/a/", "同一段原文", "chars:0-6", "demo", "2026-10-08T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    store = Store(path)
    columns = {row[1] for row in store.conn.execute("PRAGMA table_info(evidence)")}
    assert {"dimensions", "canonical_url", "content_hash", "citation_role"} <= columns
    rows = store.evidence("run_old")
    assert len(rows) == 1
    assert rows[0]["quote"] == "同一段原文"
    assert rows[0]["dimensions"] == ["产品形态"]
    assert rows[0]["canonical_url"] == "https://example.com/a"
    assert rows[0]["content_hash"]
    merged = store.add_evidence("project_old", "run_old", "research:产品A:1", {
        "title": "转载",
        "url": "https://example.com/a",
        "quote": "同一段原文",
        "locator": "chars:100-200",
        "source_type": "web",
        "dimensions": ["研究流程"],
    })
    assert merged == "ev_old"
    assert set(store.evidence("run_old")[0]["dimensions"]) == {"产品形态", "研究流程"}
    store.close()


def test_extract_dimension_passages_skips_unrelated_chunks():
    filler = ("无关介绍。" * 400) + "\n\n"
    relevant = "官方文档说明该产品的部署方式支持私有化。"
    raw = filler + relevant + "\n\n" + ("页脚版权。" * 400)
    passages = extract_dimension_passages(raw, ["部署方式"])
    assert len(passages) == 1
    assert "部署方式" in passages[0]["quote"]
    assert passages[0]["dimensions"] == ["部署方式"]
    assert passages[0]["locator"].startswith("chars:")
    assert len(passages[0]["quote"]) <= 1800
