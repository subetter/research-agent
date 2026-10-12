"""Heuristic source-conflict detection on the same subject × dimension cell."""

import re

from .freshness import UNKNOWN, annotate_evidence

CONFLICT_DIMENSIONS = {"定价", "价格", "收费"}


def subject_from_task_key(task_key: str) -> str:
    if not task_key:
        return ""
    rest = task_key[9:] if task_key.startswith("research:") else task_key
    return rest.rsplit(":", 1)[0]


def infer_dimension(item, dimensions) -> str | None:
    explicit = (item.get("dimension") or "").strip()
    if explicit:
        return explicit
    blob = f"{item.get('title') or ''} {item.get('quote') or ''}"
    for dimension in dimensions or []:
        if dimension and dimension in blob:
            return dimension
    return None


def evidence_for_cell(items, subject, dimension) -> list[dict]:
    matches = []
    for item in items:
        if subject_from_task_key(item.get("task_key") or "") != subject:
            continue
        inferred = infer_dimension(item, [dimension] if dimension else [])
        if dimension and inferred != dimension:
            if dimension not in (item.get("title") or "") and dimension not in (item.get("quote") or ""):
                continue
        matches.append(item)
    return matches


def extract_signals(text: str) -> dict:
    prices = [float(match.group(1)) for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(美元|usd|元|人民币)", text or "", flags=re.I)]
    prices.extend(float(match.group(1)) for match in re.finditer(r"[$￥]\s*(\d+(?:\.\d+)?)", text or ""))
    polarity = None
    if re.search(r"不支持|并不支持|未支持|不提供|无法", text or ""):
        polarity = "neg"
    elif re.search(r"支持|提供", text or ""):
        polarity = "pos"
    free = bool(re.search(r"免费|free plan|free tier", text or "", flags=re.I))
    paid = bool(re.search(r"收费|付费|订阅制|\bpaid\b", text or "", flags=re.I))
    return {"prices": prices, "polarity": polarity, "free": free, "paid": paid}


def signals_conflict(left, right) -> bool:
    if left["prices"] and right["prices"] and set(left["prices"]).isdisjoint(set(right["prices"])):
        return True
    if left["free"] and right["paid"] and not left["paid"]:
        return True
    if right["free"] and left["paid"] and not right["free"]:
        return True
    if left["polarity"] and right["polarity"] and left["polarity"] != right["polarity"]:
        return True
    return False


def _cluster(items: list[dict]) -> list[list[dict]]:
    clusters: list[list[dict]] = []
    for item in items:
        signal = extract_signals(item.get("quote") or "")
        placed = False
        for cluster in clusters:
            if any(signals_conflict(signal, extract_signals(member.get("quote") or "")) for member in cluster):
                continue
            cluster.append(item)
            placed = True
            break
        if not placed:
            clusters.append([item])
    return clusters


def detect_conflicts(plan, evidence) -> list[dict]:
    dimensions = (plan or {}).get("dimensions") or []
    subjects = (plan or {}).get("subjects") or []
    annotated = annotate_evidence(evidence, plan)
    found = []
    for subject in subjects:
        for dimension in dimensions:
            cell = evidence_for_cell(annotated, subject, dimension)
            if len(cell) < 2:
                continue
            clusters = [cluster for cluster in _cluster(cell) if cluster]
            if len(clusters) < 2:
                continue
            positions = []
            for index, cluster in enumerate(clusters[:4]):
                freshness_rank = {"stale": 0, "unknown": 1, "fresh": 2}
                worst = min((item.get("freshness") or UNKNOWN for item in cluster), key=lambda status: freshness_rank.get(status, 1))
                positions.append({
                    "label": f"口径 {chr(65 + index)}",
                    "text": (cluster[0].get("quote") or "")[:240],
                    "evidence_ids": [item["id"] for item in cluster],
                    "freshness": worst,
                    "freshness_label": cluster[0].get("freshness_label") if worst == cluster[0].get("freshness") else None,
                })
            found.append({
                "id": f"conflict:{subject}:{dimension}",
                "subject": subject,
                "dimension": dimension,
                "positions": positions,
            })
    return found


def is_conflict_dimension(dimension: str) -> bool:
    return (dimension or "") in CONFLICT_DIMENSIONS


def assess_gaps(plan, evidence) -> dict:
    """Uncovered cells, source conflicts, and stale/unknown evidence when the plan cares about currency."""
    from .freshness import plan_cares_about_currency

    annotated = annotate_evidence(evidence, plan)
    conflicts = detect_conflicts(plan, annotated)
    uncovered = []
    freshness_gaps = []
    gap_subjects = []
    cares = plan_cares_about_currency(plan)
    for subject in plan.get("subjects") or []:
        subject_items = [item for item in annotated if subject_from_task_key(item.get("task_key") or "") == subject]
        needed = False
        if not subject_items:
            uncovered.append({"subject": subject, "dimension": None, "reason": "no_evidence"})
            needed = True
        for dimension in plan.get("dimensions") or []:
            cell = evidence_for_cell(subject_items, subject, dimension)
            if subject_items and not cell and any(infer_dimension(item, plan.get("dimensions")) for item in subject_items):
                uncovered.append({"subject": subject, "dimension": dimension, "reason": "uncovered_dimension"})
                needed = True
            if cell and cares and all(item.get("freshness") in {"stale", "unknown"} for item in cell):
                freshness_gaps.append({
                    "subject": subject,
                    "dimension": dimension,
                    "freshness": cell[0].get("freshness"),
                    "evidence_ids": [item["id"] for item in cell],
                })
                needed = True
        if any(item["subject"] == subject for item in conflicts):
            needed = True
        if needed and subject not in gap_subjects:
            gap_subjects.append(subject)
    return {
        "gaps": gap_subjects,
        "uncovered": uncovered,
        "conflicts": conflicts,
        "freshness_gaps": freshness_gaps,
        "conflict_count": len(conflicts),
        "stale_evidence": sum(1 for item in annotated if item.get("freshness") == "stale"),
        "undated_evidence": sum(1 for item in annotated if item.get("freshness") == "unknown"),
        "fresh_evidence": sum(1 for item in annotated if item.get("freshness") == "fresh"),
    }
