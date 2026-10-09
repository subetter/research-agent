from pathlib import Path
import pytest
from app.project_mcp import IDENTITY_FIELDS, TOOL_SCHEMAS, ProjectDataTools, build_server
from app.store import Store, now
from tests.test_workbench import new_run, start, wait_status

pytest_plugins = ("tests.test_workbench",)


def add_user(store, user_id, name):
    with store.lock, store.conn:
        store.conn.execute(
            "INSERT INTO users VALUES(?,?,?,?,?,?)",
            (user_id, name, name, "hash", "user", now()),
        )


def seed_owners(tmp_path):
    store = Store(tmp_path / "mcp.sqlite")
    add_user(store, "user_a", "alice")
    add_user(store, "user_b", "bob")
    project_a = store.create_project("甲项目", "", "user_a")
    project_b = store.create_project("乙项目", "", "user_b")
    store.add_document(project_a["id"], "alice.md", "Alice 机密段落，包含定价说明。")
    store.add_document(project_b["id"], "bob.md", "Bob 机密段落，包含定价说明。")
    run_b = store.create_run(project_b["id"], "调查乙项目的定价情况", "demo", {})
    evidence_b = store.add_evidence(project_b["id"], run_b["id"], "research:乙:0", {
        "title": "乙定价",
        "url": "https://example.com/bob",
        "quote": "Bob 的定价原文",
        "locator": "paragraph:1",
        "source_type": "web",
        "dimensions": ["定价"],
    })
    return store, project_a, project_b, evidence_b


def test_tool_schemas_do_not_accept_owner():
    names = {item["name"] for item in TOOL_SCHEMAS}
    assert names == {"search_project", "read_evidence"}
    for item in TOOL_SCHEMAS:
        properties = item["parameters"]["properties"]
        assert IDENTITY_FIELDS.isdisjoint(properties)


def test_mcp_owner_isolation_rejects_model_supplied_identity(tmp_path):
    store, project_a, project_b, evidence_b = seed_owners(tmp_path)
    tools = ProjectDataTools(store, project_a["id"], "user_a")
    hits = tools.search_project("机密 定价")
    quotes = " ".join(item["quote"] + item["title"] for item in hits["candidates"])
    assert "Alice" in quotes
    assert "Bob" not in quotes
    with pytest.raises(ValueError, match="身份由服务端注入"):
        tools.search_project("机密", owner_id="user_b", project_id=project_b["id"])
    with pytest.raises(ValueError, match="身份由服务端注入"):
        tools.read_evidence(evidence_b, owner="user_b")
    hidden = tools.read_evidence(evidence_b)
    assert hidden == {"error": "not_found"}
    assert "Bob" not in str(hidden)
    store.close()


def test_mcp_server_tools_omit_identity_fields(tmp_path):
    store, project_a, _, _ = seed_owners(tmp_path)
    server = build_server(store, project_a["id"], "user_a")
    listed = server._tool_manager.list_tools()
    names = {tool.name for tool in listed}
    assert names == {"search_project", "read_evidence"}
    for tool in listed:
        schema = tool.parameters if hasattr(tool, "parameters") else getattr(tool, "inputSchema", {})
        properties = (schema or {}).get("properties") or {}
        assert IDENTITY_FIELDS.isdisjoint(properties)
    store.close()


def test_core_write_path_does_not_depend_on_mcp():
    root = Path(__file__).resolve().parents[1] / "app"
    for name in ("engine.py", "providers.py", "store.py", "main.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "project_mcp" not in text
        assert "from mcp" not in text
        assert "import mcp" not in text


async def test_research_write_path_works_with_mcp_disabled(runtime):
    engine, store = runtime
    assert engine.settings.mcp_enabled is False
    run = await new_run(engine, store, ["产品 A"])
    await start(engine, store, run)
    result = await wait_status(store, run["id"], {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    assert store.evidence(run["id"])
