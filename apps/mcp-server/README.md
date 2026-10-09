# 项目资料 MCP Server

独立进程，默认关闭。核心研究写入路径不经过这里。

```bash
MCP_ENABLED=true
MCP_DATA_DIR=./data
MCP_PROJECT_ID=当前项目
MCP_OWNER_ID=当前用户
PYTHONPATH=apps/api python apps/mcp-server/__main__.py
```

只暴露 `search_project` 与 `read_evidence`。项目与所有者从环境变量注入，工具参数里不能带 `owner` / `project_id`。
