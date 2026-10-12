"""Evidence freshness: published_at is never inferred from fetched_at."""

from datetime import datetime, timedelta, timezone

FRESH = "fresh"
STALE = "stale"
UNKNOWN = "unknown"
FRESHNESS_LABELS = {FRESH: "较新", STALE: "过时", UNKNOWN: "未标注日期"}


def parse_published_at(value) -> str | None:
    """Normalize a publisher-provided timestamp. Empty or unparseable stays empty."""
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    if not text:
        return None
    candidates = (text, text.replace("Z", "+00:00"), text.replace("/", "-"))
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            continue
    return None


def parse_as_of(value) -> datetime | None:
    normalized = parse_published_at(value)
    if not normalized:
        return None
    return datetime.fromisoformat(normalized)


def classify_freshness(published_at, *, as_of=None, max_age_days=365, fetched_at=None) -> str:
    """Classify using publish time only. fetched_at is accepted to assert it is unused."""
    del fetched_at  # never a substitute for published_at
    parsed = parse_published_at(published_at)
    if not parsed:
        return UNKNOWN
    published = datetime.fromisoformat(parsed)
    reference = parse_as_of(as_of) or datetime.now(timezone.utc)
    age = reference - published
    if age > timedelta(days=int(max_age_days or 365)):
        return STALE
    return FRESH


def plan_cares_about_currency(plan) -> bool:
    return bool((plan or {}).get("require_freshness"))


def freshness_policy(plan) -> dict:
    plan = plan or {}
    return {
        "as_of": plan.get("as_of") or None,
        "max_age_days": int(plan.get("max_evidence_age_days") or 365),
        "require_freshness": plan_cares_about_currency(plan),
    }


def annotate_evidence(items, plan=None) -> list[dict]:
    policy = freshness_policy(plan)
    annotated = []
    for item in items:
        status = classify_freshness(
            item.get("published_at"),
            as_of=policy["as_of"],
            max_age_days=policy["max_age_days"],
        )
        annotated.append({**item, "freshness": status, "freshness_label": FRESHNESS_LABELS[status]})
    return annotated


def can_reuse_evidence(item, plan, mode) -> bool:
    """Demo reuse is always allowed (sources are labeled 模拟). Live reuse never uses fetch time."""
    if mode == "demo":
        return True
    status = classify_freshness(
        item.get("published_at"),
        as_of=(plan or {}).get("as_of"),
        max_age_days=(plan or {}).get("max_evidence_age_days") or 365,
    )
    if plan_cares_about_currency(plan):
        return status == FRESH
    return status != STALE
