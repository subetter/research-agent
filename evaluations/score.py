#!/usr/bin/env python3
"""Score exported artifact JSON. Do not invent values for missing fields."""

from __future__ import annotations

import json
import statistics
from pathlib import Path


SUPPORTED = {"fully", "partial"}


def load_artifact(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _usage_totals(usage) -> tuple[int | None, int | None, int | None]:
    if not isinstance(usage, list):
        return None, None, None
    model = search = tokens = 0
    seen = False
    for row in usage:
        seen = True
        kind = row.get("kind")
        model += int(row.get("calls") or 0) if kind == "model" else 0
        search += int(row.get("calls") or 0) if kind == "search" else 0
        tokens += int(row.get("tokens") or 0)
    if not seen:
        return 0, 0, 0
    return model, search, tokens


def score_artifact(payload: dict) -> dict:
    artifact = payload.get("artifact") or {}
    claims = artifact.get("claims") or []
    evidence = payload.get("evidence") or artifact.get("evidence") or []
    valid = {item["id"] for item in evidence if item.get("id")}
    coverage = artifact.get("coverage") or payload.get("coverage") or {}
    total = coverage.get("total")
    covered = coverage.get("covered")
    if total in (None, 0) and coverage.get("cells"):
        total = len(coverage["cells"])
        covered = sum(1 for cell in coverage["cells"] if cell.get("covered"))
    coverage_rate = (covered / total) if total else None

    facts = [claim for claim in claims if claim.get("kind") == "fact"]
    if facts:
        citation_completeness = sum(
            1 for claim in facts if any(item in valid for item in claim.get("evidence_ids") or [])
        ) / len(facts)
    else:
        citation_completeness = None

    cited = [claim for claim in claims if claim.get("evidence_ids")]
    mode = payload.get("mode") or artifact.get("mode")
    if mode == "demo":
        citation_support = None
        support_note = "demo 只标 reference_checked，不计算语义支持率"
    elif cited:
        citation_support = sum(1 for claim in cited if claim.get("verification") in SUPPORTED) / len(cited)
        support_note = "cited claims with verification fully/partial"
    else:
        citation_support = 0.0
        support_note = "no cited claims"

    model_calls, search_calls, tokens = _usage_totals(payload.get("usage"))
    return {
        "task_id": payload.get("task_id"),
        "family": payload.get("family"),
        "system": payload.get("system"),
        "repeat": payload.get("repeat"),
        "status": payload.get("status") or artifact.get("status"),
        "mode": mode,
        "coverage_rate": coverage_rate,
        "citation_completeness": citation_completeness,
        "citation_support": citation_support,
        "support_note": support_note,
        "model_calls": model_calls,
        "search_calls": search_calls,
        "tokens": tokens,
        "failed": payload.get("status") not in {"completed", "partial"} or bool(payload.get("error")),
        "error": payload.get("error"),
    }


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def mean(values):
    usable = [value for value in values if value is not None]
    if not usable:
        return None
    return statistics.fmean(usable)


def summarize(rows: list[dict]) -> dict:
    systems = {}
    for row in rows:
        systems.setdefault(row["system"], []).append(row)
    summary = {}
    for name, items in systems.items():
        summary[name] = {
            "n": len(items),
            "failed": [item["task_id"] for item in items if item["failed"]],
            "coverage_rate": mean([item["coverage_rate"] for item in items]),
            "citation_completeness": mean([item["citation_completeness"] for item in items]),
            "citation_support": mean([item["citation_support"] for item in items]),
            "model_calls": mean([item["model_calls"] for item in items]),
            "search_calls": mean([item["search_calls"] for item in items]),
            "tokens": mean([item["tokens"] for item in items]),
        }
    return summary


def render_table(rows: list[dict]) -> str:
    header = f"{'task':<22} {'fam':<11} {'sys':<10} {'rep':<3} {'cov':>5} {'cite':>5} {'supp':>5} {'mdl':>4} {'srch':>4} {'tok':>5} {'status'}"
    lines = [header, "-" * len(header)]
    for row in sorted(rows, key=lambda item: (item.get("task_id") or "", item.get("system") or "", item.get("repeat") or 0)):
        lines.append(
            f"{row.get('task_id') or '':<22} {row.get('family') or '':<11} {row.get('system') or '':<10} "
            f"{row.get('repeat') or 0:<3} {_fmt(row['coverage_rate']):>5} {_fmt(row['citation_completeness']):>5} "
            f"{_fmt(row['citation_support']):>5} {_fmt(row['model_calls']):>4} {_fmt(row['search_calls']):>4} "
            f"{_fmt(row['tokens']):>5} {row.get('status') or '?'}"
        )
    summary = summarize(rows)
    lines.append("")
    lines.append("means over all repeats (not the best run):")
    for name, item in summary.items():
        failed = ",".join(item["failed"]) if item["failed"] else "none"
        lines.append(
            f"  {name}: n={item['n']} cov={_fmt(item['coverage_rate'])} cite={_fmt(item['citation_completeness'])} "
            f"support={_fmt(item['citation_support'])} model={_fmt(item['model_calls'])} "
            f"search={_fmt(item['search_calls'])} tokens={_fmt(item['tokens'])} failed={failed}"
        )
    return "\n".join(lines)


def score_dir(path: Path) -> tuple[list[dict], str]:
    rows = [score_artifact(load_artifact(file)) for file in sorted(path.glob("*.json")) if file.name != "summary.json"]
    return rows, render_table(rows)


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "results" / "latest"
    rows, table = score_dir(target)
    print(table)
    print(f"\nscored {len(rows)} artifacts from {target}")
