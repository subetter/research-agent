"""Project research tools with host-injected identity (MCP-shaped surface).

In-process tools serve the researcher and HTTP adapters. A standalone MCP
process can be started from apps/mcp-server when MCP_ENABLED is on.
Owner and project_id come from the session, never from model-supplied arguments.
"""

from __future__ import annotations

from .retrieval import read_project_chunk, search_project_documents

IDENTITY_FIELDS = frozenset({"owner", "owner_id", "project_id", "user_id"})

TOOL_SPECS = [
    {
        "name": "search_project",
        "description": "在当前项目已就绪的资料中做混合检索，返回带页码定位的原文片段。",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "read_evidence",
        "description": "按 chunk_id 或 evidence_id 读取一条已索引的项目资料原文。",
        "parameters": {"type": "object", "properties": {"chunk_id": {"type": "string"}, "evidence_id": {"type": "string"}}},
    },
]
TOOL_SCHEMAS = TOOL_SPECS


def _reject_identity(arguments: dict | None):
    extra = IDENTITY_FIELDS.intersection((arguments or {}).keys())
    if extra:
        raise ValueError("身份由服务端注入")


class ProjectToolServer:
    def __init__(self, store, settings, project_id: str, owner_id: str | None = None):
        self.store = store
        self.settings = settings
        self.project_id = project_id
        self.owner_id = owner_id
        if owner_id:
            project = store.project(project_id)
            if not project or project.get("owner_id") != owner_id:
                raise PermissionError("项目不存在或无权访问")

    def list_tools(self):
        return TOOL_SPECS

    def search_project(self, query: str) -> list[dict]:
        if not isinstance(query, str) or not 1 <= len(query) <= 1000:
            raise ValueError("查询参数无效")
        return search_project_documents(self.store, self.settings, self.project_id, query)

    def read_evidence(self, chunk_id: str | None = None, evidence_id: str | None = None) -> dict:
        if chunk_id:
            item = read_project_chunk(self.store, self.project_id, chunk_id)
            if not item:
                raise KeyError("资料片段不存在")
            return item
        if evidence_id:
            rows = self.store.query("SELECT * FROM evidence WHERE id=? AND project_id=?", (evidence_id, self.project_id))
            if not rows:
                raise KeyError("资料片段不存在")
            return rows[0]
        raise ValueError("需要 chunk_id 或 evidence_id")

    def call(self, name: str, arguments: dict) -> dict:
        _reject_identity(arguments)
        if name == "search_project":
            return {"results": self.search_project(arguments.get("query", ""))}
        if name == "read_evidence":
            return {"evidence": self.read_evidence(arguments.get("chunk_id"), arguments.get("evidence_id"))}
        raise ValueError("未知工具")


class ProjectDataTools:
    """Standalone MCP tools. Identity is bound at construction."""

    def __init__(self, store, project_id: str, owner_id: str, settings=None):
        self.store = store
        self.project_id = project_id
        self.owner_id = owner_id
        self.settings = settings
        project = store.project(project_id)
        if not project or project.get("owner_id") != owner_id:
            raise PermissionError("项目不存在或无权访问")

    def search_project(self, query: str, **identity):
        _reject_identity(identity)
        if not isinstance(query, str) or not 1 <= len(query) <= 1000:
            raise ValueError("查询参数无效")
        if self.settings is not None:
            hits = search_project_documents(self.store, self.settings, self.project_id, query)
        else:
            from .config import Settings
            hits = search_project_documents(self.store, Settings(_env_file=None), self.project_id, query)
        return {"candidates": hits}

    def read_evidence(self, evidence_id: str, **identity):
        _reject_identity(identity)
        rows = self.store.query("SELECT * FROM evidence WHERE id=? AND project_id=?", (evidence_id, self.project_id))
        if not rows:
            return {"error": "not_found"}
        return {"evidence": rows[0]}


def build_server(store, project_id: str, owner_id: str, settings=None):
    from mcp.server.fastmcp import FastMCP

    tools = ProjectDataTools(store, project_id, owner_id, settings)
    server = FastMCP("research-project")

    @server.tool()
    def search_project(query: str) -> dict:
        return tools.search_project(query)

    @server.tool()
    def read_evidence(evidence_id: str) -> dict:
        return tools.read_evidence(evidence_id)

    return server
