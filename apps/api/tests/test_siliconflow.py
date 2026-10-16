import json
import logging

import httpx
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.documents import ingest_bytes, reindex_project
from app.embeddings import (
    LocalHashEmbedder,
    LocalLexicalReranker,
    SiliconFlowEmbedder,
    SiliconFlowReranker,
    get_embedder,
    get_reranker,
    retrieval_status,
)
from app.main import create_app
from app.retrieval import search_project_documents
from app.secrets import langfuse_mask_keys, redact_text
from app.store import Store


SECRET = "sk-siliconflow-secret-value-12345"


def settings(**overrides):
    return Settings(
        _env_file=None,
        embedding_provider=overrides.pop("embedding_provider", "siliconflow"),
        embedding_model=overrides.pop("embedding_model", "BAAI/bge-m3"),
        siliconflow_api_key=overrides.pop("siliconflow_api_key", ""),
        index_version=overrides.pop("index_version", "v1"),
        embedding_max_retries=overrides.pop("embedding_max_retries", 3),
        **overrides,
    )


def reset_provider():
    import app.embeddings as emb

    emb._DIM_CACHE.clear()
    emb._TEST_TRANSPORT = None


def install_transport(handler):
    import app.embeddings as emb

    emb._TEST_TRANSPORT = httpx.MockTransport(handler)


def embedding_ok(vectors, model="BAAI/bge-m3"):
    return {
        "object": "list",
        "model": model,
        "data": [{"index": i, "object": "embedding", "embedding": vector} for i, vector in enumerate(vectors)],
        "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
    }


def test_siliconflow_without_key_falls_back_to_local():
    reset_provider()
    cfg = settings(siliconflow_api_key="")
    embedder = get_embedder(cfg)
    reranker = get_reranker(cfg)
    assert isinstance(embedder, LocalHashEmbedder)
    assert isinstance(reranker, LocalLexicalReranker)
    status = retrieval_status(cfg)
    assert status["fallback"] is True
    assert status["embedder"] == "local-hash-v1"
    assert status["reranker"] == "local-lexical-v1"


def test_explicit_local_provider_ignores_siliconflow_key():
    reset_provider()
    cfg = settings(embedding_provider="local", siliconflow_api_key=SECRET, embedding_model="local-hash-v1")
    assert isinstance(get_embedder(cfg), LocalHashEmbedder)
    assert isinstance(get_reranker(cfg), LocalLexicalReranker)
    assert retrieval_status(cfg)["fallback"] is False


def test_mocked_embeddings_and_rerank_shapes():
    reset_provider()
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append((request.url.path, body, request.headers.get("authorization")))
        if request.url.path.endswith("/embeddings"):
            assert body["model"] == "BAAI/bge-m3"
            assert body["encoding_format"] == "float"
            assert isinstance(body["input"], list)
            assert 1 <= len(body["input"]) <= 32
            assert "dimensions" not in body
            return httpx.Response(200, json=embedding_ok([[0.1, 0.2, 0.3] for _ in body["input"]]))
        if request.url.path.endswith("/rerank"):
            assert body["model"] == "BAAI/bge-reranker-v2-m3"
            assert body["query"] == "Apple"
            assert body["documents"] == ["banana", "apple"]
            assert "instruction" not in body
            return httpx.Response(
                200,
                json={"id": "rerank-test", "results": [{"index": 1, "relevance_score": 0.91}, {"index": 0, "relevance_score": 0.12}]},
            )
        return httpx.Response(404, json={"error": "missing"})

    install_transport(handler)
    cfg = settings(siliconflow_api_key=SECRET)
    embedder = get_embedder(cfg)
    vectors = embedder.embed(["hello", "world"])
    assert len(vectors) == 2
    assert vectors[0] == [0.1, 0.2, 0.3]
    assert embedder.dim == 3
    scores = get_reranker(cfg).rerank("Apple", ["banana", "apple"])
    assert scores == [0.12, 0.91]
    assert all(auth == f"Bearer {SECRET}" for _, _, auth in seen)
    assert any(path.endswith("/embeddings") for path, _, _ in seen)
    assert any(path.endswith("/rerank") for path, _, _ in seen)


def test_rerank_http_failure_degrades_to_lexical():
    reset_provider()

    def handler(request: httpx.Request):
        if request.url.path.endswith("/rerank"):
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(404, json={})

    install_transport(handler)
    cfg = settings(siliconflow_api_key=SECRET, embedding_max_retries=0)
    remote = SiliconFlowReranker(cfg)
    query, docs = "InkBot 定价", ["园艺手册", "InkBot 每月定价 20 美元"]
    assert remote.rerank(query, docs) == LocalLexicalReranker().rerank(query, docs)


def test_rate_limit_retries_then_succeeds():
    reset_provider()
    calls = {"n": 0}

    def handler(request: httpx.Request):
        calls["n"] += 1
        if request.url.path.endswith("/embeddings"):
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate"})
            return httpx.Response(200, json=embedding_ok([[0.5, 0.5]]))
        return httpx.Response(404, json={})

    install_transport(handler)
    cfg = settings(siliconflow_api_key=SECRET, embedding_max_retries=2)
    vectors = SiliconFlowEmbedder(cfg).embed(["retry me"])
    assert vectors == [[0.5, 0.5]]
    assert calls["n"] == 2


def test_embedding_batch_respects_max_32():
    reset_provider()
    sizes = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        sizes.append(len(body["input"]))
        return httpx.Response(200, json=embedding_ok([[0.1, 0.2] for _ in body["input"]]))

    install_transport(handler)
    cfg = settings(siliconflow_api_key=SECRET, embedding_batch_size=32)
    texts = [f"chunk-{i}" for i in range(40)]
    vectors = SiliconFlowEmbedder(cfg).embed(texts)
    assert len(vectors) == 40
    assert sizes == [32, 8]


def test_reindex_on_model_and_dim_change(tmp_path):
    reset_provider()
    store = Store(tmp_path / "reindex.sqlite")
    project = store.create_project("重建", "")
    local = settings(embedding_provider="local", embedding_model="local-hash-v1")
    ingest_bytes(store, local, project["id"], "note.md", "混合检索需要记录 embedding_model 与 dims。".encode())
    first = store.chunks_for_document(store.list_documents(project["id"])[0]["id"])
    assert first[0]["embedding_model"] == LocalHashEmbedder.name
    assert first[0]["embedding_dim"] == 64

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json=embedding_ok([[0.1, 0.2, 0.3, 0.4] for _ in body["input"]]))
        if request.url.path.endswith("/rerank"):
            return httpx.Response(200, json={"id": "r", "results": [{"index": i, "relevance_score": 0.5} for i in range(len(body["documents"]))]})
        return httpx.Response(404, json={})

    install_transport(handler)
    remote = settings(siliconflow_api_key=SECRET)
    hits = search_project_documents(store, remote, project["id"], "embedding_model")
    assert hits
    refreshed = store.chunks_for_document(store.list_documents(project["id"])[0]["id"])
    assert refreshed[0]["embedding_model"] == "BAAI/bge-m3"
    assert refreshed[0]["embedding_dim"] == 4
    leftover = store.chunks_for_project(project["id"], LocalHashEmbedder.name, "v1", 64)
    assert leftover == []

    store.conn.execute("UPDATE document_chunks SET embedding_dim=?", (16,))
    store.conn.commit()
    search_project_documents(store, remote, project["id"], "dims")
    again = store.chunks_for_document(store.list_documents(project["id"])[0]["id"])
    assert again[0]["embedding_dim"] == 4
    store.close()


def test_key_is_redacted_and_never_logged(caplog):
    reset_provider()
    cfg = settings(siliconflow_api_key=SECRET)
    assert SECRET not in redact_text(f"Authorization: Bearer {SECRET}", cfg)
    assert "[REDACTED]" in redact_text(SECRET, cfg)
    assert "SILICONFLOW_API_KEY" in langfuse_mask_keys()

    def handler(request: httpx.Request):
        return httpx.Response(200, json=embedding_ok([[0.2, 0.1]]))

    install_transport(handler)
    with caplog.at_level(logging.DEBUG):
        SiliconFlowEmbedder(cfg).embed(["不要把密钥写进日志"])
    assert SECRET not in caplog.text
    assert "Bearer sk-siliconflow" not in caplog.text


async def test_force_reindex_api(tmp_path):
    reset_provider()
    app = create_app(settings(embedding_provider="local", embedding_model="local-hash-v1", data_dir=str(tmp_path), siliconflow_api_key=""))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/api/auth/register", json={"username": "sf_user", "display_name": "SF", "password": "test-password-123"})
            project = (await client.post("/api/projects", json={"name": "资料库"})).json()
            uploaded = await client.post(
                f"/api/projects/{project['id']}/documents",
                files={"file": ("note.md", b"SiliconFlow reindex keeps vector spaces apart.", "text/markdown")},
            )
            assert uploaded.status_code == 201
            health = (await client.get("/api/health")).json()
            assert health["retrieval"]["embedder"] == "local-hash-v1"
            forced = await client.post(f"/api/projects/{project['id']}/documents/reindex", json={"force": True})
            assert forced.status_code == 200
            body = forced.json()
            assert body["reindexed"] == 1
            assert body["embedder"] == "local-hash-v1"
            assert body["embedding_dim"] == 64


def test_reindex_helper_reports_current_space(tmp_path):
    reset_provider()
    store = Store(tmp_path / "cli.sqlite")
    project = store.create_project("命令", "")
    ingest_bytes(store, settings(embedding_provider="local", embedding_model="local-hash-v1"), project["id"], "a.md", "重建索引命令。".encode())
    result = reindex_project(store, settings(embedding_provider="local", embedding_model="local-hash-v1"), project["id"], force=True)
    assert result["reindexed"] == 1
    assert result["embedder"] == "local-hash-v1"
    assert result["embedding_dim"] == 64
    store.close()
