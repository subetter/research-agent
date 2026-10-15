"""Sanitized Markdown / HTML / website export. Source text is never injected raw."""

from __future__ import annotations

import html
import io
import re
import zipfile
from urllib.parse import urlparse

CITE_RE = re.compile(r"\[\[([A-Za-z0-9_-]+)\]\]")
UNSAFE_URL = re.compile(r"^\s*(javascript|data|vbscript):", re.I)


def escape_text(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def safe_url(url: str) -> str:
    text = (url or "").strip()
    if not text or UNSAFE_URL.match(text):
        return ""
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        return text
    if text.startswith("project://"):
        return ""
    return ""


def slug(value: str, fallback: str = "section") -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (value or "").strip())
    text = text.strip("-")[:48] or fallback
    return text


def evidence_index(evidence: list[dict]) -> dict[str, dict]:
    return {item["id"]: item for item in evidence or [] if item.get("id")}


def split_cited(body: str):
    text = body or ""
    cursor = 0
    for match in CITE_RE.finditer(text):
        if match.start() > cursor:
            yield ("text", text[cursor:match.start()])
        yield ("cite", match.group(1))
        cursor = match.end()
    if cursor < len(text):
        yield ("text", text[cursor:])


def cited_markdown(body: str, quotes: dict[str, dict]) -> str:
    parts = []
    for kind, value in split_cited(body):
        if kind == "text":
            parts.append(value)
            continue
        item = quotes.get(value)
        if not item:
            parts.append(f"[缺失引用 {value}]")
            continue
        parts.append(f"[{value}]({item.get('url') or '#'})")
    return "".join(parts)


def cited_html(body: str, quotes: dict[str, dict]) -> str:
    parts = []
    for kind, value in split_cited(body):
        if kind == "text":
            parts.append(escape_text(value).replace("\n", "<br/>"))
            continue
        item = quotes.get(value)
        if not item:
            parts.append(f'<span class="cite missing">{escape_text(value)}</span>')
            continue
        href = safe_url(item.get("url") or "")
        title = escape_text(item.get("quote") or "")
        label = escape_text(value)
        link = f' href="{escape_text(href)}"' if href else ""
        parts.append(f'<a class="cite"{link} title="{title}">{label}</a>')
    return "".join(parts)


def report_fields(artifact: dict) -> dict:
    artifact = artifact or {}
    sections = list(artifact.get("sections") or [])
    outline = list(artifact.get("outline") or [])
    if not sections and artifact.get("claims"):
        grouped = {}
        for claim in artifact["claims"]:
            grouped.setdefault(claim.get("subject") or "主题", []).append(claim)
        for subject, claims in grouped.items():
            ident = slug(str(subject))
            body = []
            ids = []
            for claim in claims:
                cites = "".join(f"[[{item}]]" for item in claim.get("evidence_ids") or [])
                body.append(f"{claim.get('dimension') or ''}：{claim.get('text') or ''} {cites}".strip())
                ids.extend(claim.get("evidence_ids") or [])
            sections.append({"id": ident, "title": subject, "body": "\n\n".join(body), "evidence_ids": list(dict.fromkeys(ids))})
            outline.append({"id": ident, "title": subject})
    if not outline:
        outline = [{"id": item.get("id") or slug(item.get("title") or "section"), "title": item.get("title") or "章节"} for item in sections]
    sources = list(artifact.get("sources") or [])
    return {
        "title": artifact.get("title") or "研究报告",
        "summary": artifact.get("summary") or "",
        "outline": outline,
        "sections": sections,
        "open_questions": list(artifact.get("open_questions") or []),
        "sources": sources,
        "charts": list(artifact.get("charts") or []),
        "tables": list(artifact.get("tables") or []),
        "claims": list(artifact.get("claims") or []),
        "verification_note": artifact.get("verification_note") or "",
        "mode": artifact.get("mode") or "",
    }


def sources_from_evidence(evidence: list[dict]) -> list[dict]:
    rows = []
    for item in evidence or []:
        rows.append({
            "id": item.get("id"),
            "title": item.get("title") or item.get("id"),
            "url": item.get("url") or "",
            "locator": item.get("locator") or "",
            "quote": item.get("quote") or "",
        })
    return rows


def markdown_report(artifact: dict, evidence: list[dict], mode: str = "") -> str:
    report = report_fields(artifact)
    quotes = evidence_index(evidence)
    lines = [f"# {report['title']}", ""]
    if mode or report["verification_note"]:
        lines.extend([f"> 模式：{mode or report['mode']}。{report['verification_note']}", ""])
    if report["summary"]:
        lines.extend(["## 摘要", "", report["summary"], ""])
    if report["outline"]:
        lines.extend(["## 目录", ""])
        for item in report["outline"]:
            lines.append(f"- {item.get('title')}")
        lines.append("")
    for section in report["sections"]:
        lines.extend([f"## {section.get('title') or '章节'}", "", cited_markdown(section.get("body") or "", quotes), ""])
    if report["claims"]:
        lines.extend(["## 结论条目", ""])
        for claim in report["claims"]:
            lines.extend([
                f"### {claim.get('subject')} · {claim.get('dimension')}",
                "",
                claim.get("text") or "",
                "",
                f"核验：{claim.get('verification', '')}",
                "",
                "证据：" + ", ".join(claim.get("evidence_ids") or []),
                "",
            ])
    if report["open_questions"]:
        lines.extend(["## 未解问题", ""])
        for item in report["open_questions"]:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("## 来源")
    for ev in sources_from_evidence(evidence) or report["sources"]:
        lines.extend(["", f"### {ev.get('id')}", "", f"{ev.get('title')} · {ev.get('url')} · {ev.get('locator')}", "", ev.get("quote") or "", ""])
    return "\n".join(lines)


def _css() -> str:
    return """
:root { --ink:#2b2a3d; --muted:#6b6980; --line:#e6e4f2; --paper:#fff; --bg:#f6f5fb; --accent:#5b4fc9; }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body { margin:0; font-family:'Iwanami','Source Han Serif SC','Noto Serif SC','Georgia',serif; color:var(--ink); background:var(--bg); line-height:1.85; }
.shell { display:grid; grid-template-columns:240px 1fr; min-height:100vh; }
nav { position:sticky; top:0; height:100vh; overflow:auto; padding:32px 22px; background:#efedf8; border-right:1px solid var(--line); }
nav a { display:block; color:#4d4870; text-decoration:none; font-size:14px; margin:8px 0; }
nav .brand { font-weight:700; font-family:'DM Sans',sans-serif; letter-spacing:.04em; margin-bottom:18px; }
main { max-width:820px; padding:48px 56px 80px; }
h1,h2,h3 { font-weight:600; line-height:1.4; }
h1 { font-size:34px; margin:0 0 18px; }
h2 { font-size:24px; margin:42px 0 14px; }
.summary { font-size:18px; color:#3d3a55; }
.cite { color:var(--accent); font-size:12px; font-family:'DM Sans',sans-serif; text-decoration:none; border-bottom:1px dotted #b9b3e3; }
table { border-collapse:collapse; width:100%; font-size:14px; background:var(--paper); }
th,td { border:1px solid var(--line); padding:10px 12px; vertical-align:top; }
.chart { margin:24px 0; background:var(--paper); padding:16px; border:1px solid var(--line); }
.source { background:var(--paper); border:1px solid var(--line); padding:16px 18px; margin:14px 0; }
.source blockquote { margin:8px 0 0; color:var(--muted); white-space:pre-wrap; }
.note { color:var(--muted); font-size:13px; }
@media(max-width:800px){ .shell{display:block;} nav{height:auto; position:relative;} main{padding:28px 20px;} }
"""


def _bar_chart_svg(chart: dict) -> str:
    labels = [str(item) for item in (chart.get("labels") or [])][:12]
    raw_values = chart.get("values") or []
    values = []
    for item in raw_values[:12]:
        try:
            values.append(float(item))
        except (TypeError, ValueError):
            values.append(0.0)
    if not labels or not values:
        return ""
    width = 560
    height = 220
    pad = 36
    bar_w = max(12, (width - 2 * pad) / max(1, len(labels)) - 12)
    peak = max(values) or 1
    bars = []
    for index, (label, value) in enumerate(zip(labels, values)):
        h = max(1, (value / peak) * (height - 70))
        x = pad + index * ((width - 2 * pad) / max(1, len(labels)))
        y = height - 32 - h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="#7b6ee0"/>'
            f'<text x="{x + bar_w / 2:.1f}" y="{height - 12}" text-anchor="middle" font-size="11">{escape_text(label)}</text>'
        )
    title = escape_text(chart.get("title") or "图表")
    return f'<div class="chart"><h3>{title}</h3><svg viewBox="0 0 {width} {height}" role="img" aria-label="{title}">{"".join(bars)}</svg></div>'


def _table_html(table: dict) -> str:
    headers = [escape_text(item) for item in (table.get("headers") or [])]
    rows = table.get("rows") or []
    head = "".join(f"<th>{item}</th>" for item in headers)
    body = []
    for row in rows[:40]:
        cells = "".join(f"<td>{escape_text(cell)}</td>" for cell in (row or [])[:20])
        body.append(f"<tr>{cells}</tr>")
    return f'<h2>{escape_text(table.get("title") or "对照表")}</h2><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def website_html(artifact: dict, evidence: list[dict], *, mode: str = "", note: str = "") -> str:
    report = report_fields(artifact)
    quotes = evidence_index(evidence)
    sources = sources_from_evidence(evidence) or report["sources"]
    nav = ['<div class="brand">Research Report</div>', '<a href="#summary">摘要</a>']
    for item in report["outline"]:
        ident = escape_text(item.get("id") or slug(item.get("title") or "section"))
        nav.append(f'<a href="#{ident}">{escape_text(item.get("title") or "章节")}</a>')
    if report["tables"]:
        nav.append('<a href="#tables">对照表</a>')
    if report["charts"]:
        nav.append('<a href="#charts">图表</a>')
    nav.append('<a href="#unknowns">未解问题</a>')
    nav.append('<a href="#citations">来源</a>')
    sections = []
    for section in report["sections"]:
        ident = escape_text(section.get("id") or slug(section.get("title") or "section"))
        sections.append(f'<section id="{ident}"><h2>{escape_text(section.get("title") or "章节")}</h2><p>{cited_html(section.get("body") or "", quotes)}</p></section>')
    tables = "".join(_table_html(table) for table in report["tables"])
    charts = "".join(_bar_chart_svg(chart) for chart in report["charts"])
    unknowns = "".join(f"<li>{escape_text(item)}</li>" for item in report["open_questions"])
    citations = []
    for ev in sources:
        href = safe_url(ev.get("url") or "")
        link = f'<a href="{escape_text(href)}">{escape_text(href)}</a>' if href else ""
        citations.append(
            f'<article class="source" id="{escape_text(ev.get("id") or "")}">'
            f'<h3>{escape_text(ev.get("title") or ev.get("id"))}</h3>'
            f'<p>{link} · {escape_text(ev.get("locator") or "")}</p>'
            f'<blockquote>{escape_text(ev.get("quote") or "")}</blockquote></article>'
        )
    banner = escape_text(note or report["verification_note"])
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"/>"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>"
        f"<title>{escape_text(report['title'])}</title><style>{_css()}</style></head><body>"
        f"<div class=\"shell\"><nav>{''.join(nav)}</nav><main>"
        f"<h1>{escape_text(report['title'])}</h1>"
        f"<p class=\"note\">{escape_text(mode)} {banner}</p>"
        f"<section id=\"summary\"><h2>摘要</h2><p class=\"summary\">{escape_text(report['summary'])}</p></section>"
        f"{''.join(sections)}"
        f"<section id=\"tables\">{tables}</section>"
        f"<section id=\"charts\">{charts}</section>"
        f"<section id=\"unknowns\"><h2>未解问题</h2><ul>{unknowns or '<li>无</li>'}</ul></section>"
        f"<section id=\"citations\"><h2>来源</h2>{''.join(citations)}</section>"
        "</main></div></body></html>"
    )


def website_zip(artifact: dict, evidence: list[dict], *, mode: str = "", note: str = "") -> bytes:
    page = website_html(artifact, evidence, mode=mode, note=note)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", page)
    return buffer.getvalue()
