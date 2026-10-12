"""Project research tools with host-injected identity (MCP-shaped surface).

Main has no standalone MCP process. This module is the tool server the researcher
and HTTP adapters call. Owner and project_id come from the session, never from
model-supplied arguments.
"""

from __future__ import annotations

from .retrieval import read_project_chunk, search_project_documents


TOOL_SPECS = [
    {
        "name": "search_project",
        "description": "在当前项目已就绪的资料中做混合检索，返回带页码定位的原文片段。",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "read_evidence",
        "description": "按 chunk_id 读取一条已索引的项目资料原文。",
        "parameters": {"type": "object", "properties": {"chunk_id": {"type": "string"}}, "required": ["chunk_id"]},
    },
]


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

    def read_evidence(self, chunk_id: str) -> dict:
        item = read_project_chunk(self.store, self.project_id, chunk_id)
        if not item:
            raise KeyError("资料片段不存在")
        return item

    def call(self, name: str, arguments: dict) -> dict:
        if name == "search_project":
            return {"results": self.search_project(arguments.get("query", ""))}
        if name == "read_evidence":
            return {"evidence": self.read_evidence(arguments.get("chunk_id", ""))}
        raise ValueError("未知工具")
