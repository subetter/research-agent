#!/usr/bin/env python3
"""Write pinned Tavily/model HTTP fixtures from catalog.py."""

from __future__ import annotations

import json
from pathlib import Path

from catalog import TASKS, compile_task

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"


def write_fixtures() -> list[Path]:
    written = []
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for task in TASKS:
        compiled = compile_task(task)
        dest = FIXTURES / task["id"]
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("task", "tavily", "model", "policies"):
            path = dest / f"{name}.json"
            path.write_text(json.dumps(compiled[name], ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(path)
    (FIXTURES / "index.json").write_text(
        json.dumps([task["id"] for task in TASKS], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return written


if __name__ == "__main__":
    paths = write_fixtures()
    print(f"wrote {len(paths)} fixture files under {FIXTURES}")
