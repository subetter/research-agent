"""Rebuild project document vectors for the current embedding provider.

  python -m app.reindex --project PROJECT_ID
  python -m app.reindex --all --force
"""

from __future__ import annotations

import argparse
import sys

from .config import Settings
from .documents import reindex_project
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild document embeddings for the current provider.")
    parser.add_argument("--project", help="Project id to rebuild")
    parser.add_argument("--all", action="store_true", help="Rebuild every project")
    parser.add_argument("--force", action="store_true", help="Rebuild even when model/version/dims already match")
    args = parser.parse_args(argv)
    if not args.project and not args.all:
        parser.error("pass --project ID or --all")
    settings = Settings()
    store = Store(settings.data_path / "workbench.sqlite")
    try:
        result = reindex_project(store, settings, None if args.all else args.project, force=args.force)
        print(
            f"reindexed={result['reindexed']} embedder={result['embedder']} "
            f"dim={result.get('embedding_dim')} version={result['index_version']}"
        )
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
