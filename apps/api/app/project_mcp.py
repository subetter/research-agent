"""Read-only project data tools for a separate MCP process. Identity is injected here."""

from __future__ import annotations

import re
from typing import Any

IDENTITY_FIELDS = frozenset({"owner", "owner_id", "project_id", "user_id"})
TOOL_SCHEMAS = (
    {
        "name": "search_project",
        "description": "搜索当前项目资料，只返回候选段落。",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "read_evidence",
        "description": "按 ID 读取当前项目中的一条原文证据。",
        "parameters": {"type": "object", "properties": {"evidence_id": {"type": "string"}}, "required": ["evidence_id"]},
    },
)


class ProjectDataTools:
    def __init__(self, store, project_id: str, owner_id: str):
        project = store.project(project_id)
        if not project or project.get("owner_id") != owner_id:
            raise PermissionError("项目不存在或身份不匹配")
        self.store = store
        self.project_id = project_id
        self.owner_id = owner_id

    def _reject_identity(self, kwargs: dict[str, Any]):
        leaked = IDENTITY_FIELDS & set(kwargs)
        if leaked:
            raise ValueError("身份由服务端注入，模型不能传入 " + ",".join(sorted(leaked)))

    def _owned_project(self) -> bool:
        project = self.store.project(self.project_id)
        return bool(project and project.get("owner_id") == self.owner_id)

    def search_project(self, query: str, **kwargs):
        self._reject_identity(kwargs)
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("查询参数无效")
        if not self._owned_project():
            return {"candidates": []}
        terms = re.findall(r"[\w\u4e00-\u9fff]+", query.lower())
        candidates = []
        for doc in self.store.query("SELECT * FROM documents WHERE project_id=?", (self.project_id,)):
            for index, paragraph in enumerate((doc.get("content") or "").split("\n\n")):
                if not paragraph.strip():
                    continue
                score = sum(term in paragraph.lower() for term in terms)
                if score:
                    candidates.append((score, {
                        "title": doc["name"],
                        "document_id": doc["id"],
                        "quote": paragraph[:2000],
                        "locator": f"paragraph:{index + 1}",
                    }))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return {"candidates": [item for _, item in candidates[:4]]}

    def read_evidence(self, evidence_id: str, **kwargs):
        self._reject_identity(kwargs)
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("证据 ID 无效")
        rows = self.store.query("SELECT * FROM evidence WHERE id=?", (evidence_id,))
        if not rows:
            return {"error": "not_found"}
        item = rows[0]
        if item.get("project_id") != self.project_id or not self._owned_project():
            return {"error": "not_found"}
        return {
            "id": item["id"],
            "title": item["title"],
            "url": item["url"],
            "quote": item["quote"],
            "locator": item["locator"],
            "source_type": item.get("source_type"),
        }


def build_server(store, project_id: str, owner_id: str):
    from mcp.server.fastmcp import FastMCP

    tools = ProjectDataTools(store, project_id, owner_id)
    mcp = FastMCP("project-data")

    @mcp.tool()
    def search_project(query: str) -> dict:
        return tools.search_project(query)

    @mcp.tool()
    def read_evidence(evidence_id: str) -> dict:
        return tools.read_evidence(evidence_id)

    return mcp
