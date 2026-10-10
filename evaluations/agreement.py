#!/usr/bin/env python3
"""Compute verifier-vs-human agreement after the sampling sheet is filled."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from live_eval import read_samples_csv


def agreement_from_rows(rows: list[dict]) -> dict:
    labeled = []
    for row in rows:
        human = (row.get("human_label") or "").strip()
        if not human:
            continue
        labeled.append({
            "sample_id": row.get("sample_id"),
            "verifier_label": (row.get("verifier_label") or "").strip(),
            "human_label": human,
        })
    if not labeled:
        return {
            "n": 0,
            "agree": 0,
            "rate": None,
            "note": "还没有填写 human_label，无法计算一致率。",
            "pairs": [],
        }
    agree = sum(1 for row in labeled if row["verifier_label"] == row["human_label"])
    pairs = Counter((row["verifier_label"] or "empty", row["human_label"]) for row in labeled)
    return {
        "n": len(labeled),
        "agree": agree,
        "rate": agree / len(labeled),
        "note": "一致率按核验标签与人工标签完全相同计算。",
        "pairs": [{"verifier": left, "human": right, "count": count} for (left, right), count in sorted(pairs.items())],
    }


def render_agreement(result: dict) -> str:
    if result["n"] == 0:
        return result["note"]
    lines = [
        result["note"],
        f"已标注 {result['n']} 条，一致 {result['agree']} 条，一致率 {result['rate']:.2f}。",
    ]
    for pair in result["pairs"]:
        lines.append(f"- 核验 {pair['verifier'] or '（空）'} / 人工 {pair['human']}：{pair['count']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="计算抽样表中核验标签与人工标签的一致率")
    parser.add_argument("csv", help="已填写 human_label 的 samples.csv")
    args = parser.parse_args()
    path = Path(args.csv)
    result = agreement_from_rows(read_samples_csv(path))
    print(render_agreement(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
