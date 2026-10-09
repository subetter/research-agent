"""Standalone MCP process. project_id and owner come from the environment, never from tool arguments."""

from __future__ import annotations

import os
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.project_mcp import build_server
from app.store import Store


def main():
    project_id = os.environ.get("MCP_PROJECT_ID", "").strip()
    owner_id = os.environ.get("MCP_OWNER_ID", "").strip()
    data_dir = Path(os.environ.get("MCP_DATA_DIR") or os.environ.get("DATA_DIR") or "./data")
    if not project_id or not owner_id:
        raise SystemExit("MCP_PROJECT_ID 与 MCP_OWNER_ID 必须由启动方注入，不能留空")
    db = data_dir if data_dir.suffix == ".sqlite" else data_dir / "workbench.sqlite"
    store = Store(db)
    build_server(store, project_id, owner_id).run()


if __name__ == "__main__":
    main()
