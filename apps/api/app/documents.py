"""Upload ingest: format check, parse, page/paragraph chunks, index, ready/failed."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .embeddings import get_embedder, pack_vector
from .store import now, uid

PARSER_VERSION = "pymupdf-text-v1"
TEXT_SUFFIXES = {".txt", ".md", ".csv"}
PDF_SUFFIXES = {".pdf"}
ALLOWED_SUFFIXES = TEXT_SUFFIXES | PDF_SUFFIXES
MAX_UPLOAD_BYTES = 5_000_000


class ParseError(ValueError):
    pass


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def chunk_text(text: str, page: int) -> list[dict]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]
    if not paragraphs and (text or "").strip():
        paragraphs = [text.strip()]
    chunks = []
    for index, paragraph in enumerate(paragraphs, start=1):
        step = 1200
        for start in range(0, len(paragraph), step):
            piece = paragraph[start : start + step]
            locator = f"page:{page};paragraph:{index};chars:{start}-{start + len(piece)}"
            chunks.append({
                "page": page,
                "paragraph": index,
                "char_start": start,
                "char_end": start + len(piece),
                "locator": locator,
                "text": piece,
                "content_hash": content_hash(piece),
            })
    return chunks


def parse_text(raw: bytes, suffix: str) -> tuple[str, list[dict], int]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError("文本文件需要 UTF-8 编码") from exc
    if suffix == ".csv" and not text.strip():
        raise ParseError("CSV 文件是空的")
    chunks = chunk_text(text, page=1)
    if not chunks:
        raise ParseError("文件没有可检索的正文")
    return text, chunks, 1


def parse_pdf(raw: bytes) -> tuple[str, list[dict], int]:
    try:
        import pymupdf
    except ImportError as exc:
        raise ParseError("服务未安装 PDF 解析依赖") from exc
    try:
        document = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise ParseError("PDF 无法打开或已损坏") from exc
    try:
        pages = []
        chunks = []
        for number, page in enumerate(document, start=1):
            page_text = page.get_text("text") or ""
            pages.append(page_text)
            chunks.extend(chunk_text(page_text, number))
    finally:
        document.close()
    combined = "\n\n".join(part for part in pages if part.strip())
    if not chunks:
        raise ParseError("PDF 未能提取到文本（可能是扫描件、加密文件或空白页）")
    return combined, chunks, len(pages)


def parse_upload(name: str, raw: bytes) -> tuple[str, list[dict], int, str]:
    suffix = Path(name).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        text, chunks, pages = parse_text(raw, suffix)
        return text, chunks, pages, "text"
    if suffix in PDF_SUFFIXES:
        text, chunks, pages = parse_pdf(raw)
        return text, chunks, pages, "pdf"
    raise ParseError("仅支持 TXT、Markdown、CSV 与 PDF")


def ingest_bytes(store, settings, project_id: str, name: str, raw: bytes) -> dict:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ParseError(f"文件最大 {MAX_UPLOAD_BYTES // 1_000_000} MB")
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ParseError("仅支持 TXT、Markdown、CSV 与 PDF")
    digest = hashlib.sha256(raw).hexdigest()
    document = store.create_document(project_id, name, status="processing", source_type="pdf" if suffix == ".pdf" else "text", bytes=len(raw), content_hash=digest)
    try:
        text, chunks, page_count, source_type = parse_upload(name, raw)
        store.update_document(document["id"], status="ready", error=None, content=text, page_count=page_count, source_type=source_type, parser_version=PARSER_VERSION, content_hash=digest, bytes=len(raw))
        index_document(store, settings, document["id"], chunks)
        return store.document(document["id"])
    except Exception as exc:
        message = str(exc)[:400] if isinstance(exc, ParseError) else "解析失败，文件未加入检索。"
        store.update_document(document["id"], status="failed", error=message, content="", page_count=0, parser_version=PARSER_VERSION)
        store.replace_chunks(document["id"], [])
        return store.document(document["id"])


def ingest_text(store, settings, project_id: str, name: str, content: str) -> dict:
    return ingest_bytes(store, settings, project_id, name, (content or "").encode("utf-8"))


def index_document(store, settings, document_id: str, chunks: list[dict]):
    document = store.document(document_id)
    embedder = get_embedder(settings)
    index_version = settings.index_version
    texts = [item["text"] for item in chunks]
    vectors = embedder.embed(texts) if texts else []
    rows = []
    for item, vector in zip(chunks, vectors):
        rows.append({
            "id": uid("chk"),
            "document_id": document_id,
            "project_id": document["project_id"],
            "page": item["page"],
            "paragraph": item["paragraph"],
            "char_start": item["char_start"],
            "char_end": item["char_end"],
            "locator": item["locator"],
            "text": item["text"],
            "content_hash": item["content_hash"],
            "embedding": pack_vector(vector),
            "embedding_model": embedder.name,
            "index_version": index_version,
        })
    store.replace_chunks(document_id, rows)


def reindex_project(store, settings, project_id: str | None = None) -> int:
    """Rebuild vectors when embedding_model or index_version changes. Never mix spaces."""
    embedder = get_embedder(settings)
    documents = store.documents_for_reindex(project_id)
    changed = 0
    for document in documents:
        if document.get("status") != "ready":
            continue
        stale = store.chunks_need_reindex(document["id"], embedder.name, settings.index_version)
        existing = store.chunks_for_document(document["id"])
        if not existing and document.get("content"):
            index_document(store, settings, document["id"], chunk_text(document["content"], page=1))
            changed += 1
            continue
        if not stale:
            continue
        rebuilt = [{
            "page": item.get("page") or 1,
            "paragraph": item.get("paragraph") or 1,
            "char_start": item.get("char_start") or 0,
            "char_end": item.get("char_end") or len(item.get("text") or ""),
            "locator": item["locator"],
            "text": item["text"],
            "content_hash": item.get("content_hash") or content_hash(item["text"]),
        } for item in existing]
        index_document(store, settings, document["id"], rebuilt)
        changed += 1
    return changed
