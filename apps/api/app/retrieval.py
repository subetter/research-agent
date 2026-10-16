"""Hybrid project retrieval: FTS5 + vectors, hash dedupe, RRF, lexical rerank."""

from __future__ import annotations

import re

from .documents import reindex_project
from .embeddings import cosine, get_embedder, get_reranker, tokenize, unpack_vector


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return [item_id for item_id, _ in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)]


def escape_fts_term(term: str) -> str | None:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "", term)
    if not cleaned:
        return None
    return '"' + cleaned.replace('"', "") + '"'


def fts_match_query(query: str) -> str:
    terms = [escape_fts_term(token) for token in tokenize(query)]
    parts = [term for term in terms if term]
    return " OR ".join(parts[:16])


async def asearch_project_documents(store, settings, project_id: str, query: str, limit: int | None = None) -> list[dict]:
    """Run hybrid retrieval off the event loop so embed/rerank cannot stall SSE."""
    import asyncio

    return await asyncio.to_thread(search_project_documents, store, settings, project_id, query, limit)


def search_project_documents(store, settings, project_id: str, query: str, limit: int | None = None) -> list[dict]:
    """Recall only ready chunks that already belong to this project."""
    if not query or not str(query).strip():
        return []
    reindex_project(store, settings, project_id)
    embedder = get_embedder(settings)
    reranker = get_reranker(settings)
    fts_limit = int(getattr(settings, "fts_recall", 20) or 20)
    vector_limit = int(getattr(settings, "vector_recall", 20) or 20)
    top_k = limit or int(getattr(settings, "rerank_top_k", 8) or 8)
    top_k = max(6, min(10, top_k)) if limit is None else max(1, min(20, top_k))

    known_dim = embedder.dim or store.known_embedding_dim(project_id, embedder.name, settings.index_version)
    if known_dim and not embedder.dim:
        embedder.dim = known_dim
    match = fts_match_query(query)
    lexical_ids = store.fts_search(project_id, match, fts_limit, embedder.name, settings.index_version, known_dim) if match else []
    project_chunks = store.chunks_for_project(project_id, embedder.name, settings.index_version, known_dim)
    query_vec = embedder.embed([query])[0]
    query_dim = len(query_vec) if query_vec else known_dim
    scored = []
    for chunk in project_chunks:
        vector = unpack_vector(chunk.get("embedding"))
        stored_dim = chunk.get("embedding_dim") or len(vector)
        if query_dim and stored_dim and stored_dim != query_dim:
            continue
        scored.append((cosine(query_vec, vector), chunk["id"]))
    scored.sort(reverse=True)
    vector_ids = [chunk_id for score, chunk_id in scored[:vector_limit] if score > 0]

    fused = rrf_fuse([lexical_ids, vector_ids])
    by_id = {chunk["id"]: chunk for chunk in project_chunks}
    deduped = []
    seen_hash = set()
    for chunk_id in fused:
        chunk = by_id.get(chunk_id)
        if not chunk:
            continue
        digest = chunk.get("content_hash")
        if digest in seen_hash:
            continue
        seen_hash.add(digest)
        deduped.append(chunk)

    if not deduped:
        return []
    scores = reranker.rerank(query, [chunk["text"] for chunk in deduped])
    ranked = [chunk for _, chunk in sorted(zip(scores, deduped), key=lambda pair: pair[0], reverse=True)]
    return [hit_to_evidence(store, chunk) for chunk in ranked[:top_k]]


def hit_to_evidence(store, chunk: dict) -> dict:
    document = store.document(chunk["document_id"])
    title = document["name"] if document else "项目资料"
    page = chunk.get("page")
    return {
        "title": f"{title} · 第 {page} 页" if page else title,
        "url": f"project://{chunk['document_id']}#page={page or 1}",
        "quote": (chunk.get("text") or "")[:2000],
        "locator": chunk["locator"],
        "source_type": "document",
        "page": page,
        "chunk_id": chunk["id"],
        "document_id": chunk["document_id"],
        "published_at": None,
    }


def read_project_chunk(store, project_id: str, chunk_id: str) -> dict | None:
    chunk = store.chunk(chunk_id)
    if not chunk or chunk["project_id"] != project_id:
        return None
    document = store.document(chunk["document_id"])
    if not document or document.get("status") != "ready":
        return None
    return hit_to_evidence(store, chunk)
