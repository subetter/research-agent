import hashlib

from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.documents import ParseError, ingest_bytes, parse_pdf
from app.embeddings import LocalHashEmbedder, get_embedder
from app.project_mcp import ProjectToolServer
from app.retrieval import rrf_fuse, search_project_documents
from app.store import Store


def settings(**overrides):
    return Settings(_env_file=None, embedding_provider="local", index_version=overrides.pop("index_version", "v1"), **overrides)


def make_pdf(pages: list[str]) -> bytes:
    import pymupdf
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_text((72, 72), text, fontname="china-s")
    payload = document.tobytes()
    document.close()
    return payload


def test_pdf_parse_keeps_page_locators():
    raw = make_pdf(["InkBot 官方定价为每月 20 美元。", "部署方式为团队私有化，不支持共享租户。"])
    text, chunks, pages = parse_pdf(raw)
    assert pages == 2
    assert "20 美元" in text
    assert any(item["page"] == 1 and "定价" in item["text"] for item in chunks)
    assert any(item["page"] == 2 and item["locator"].startswith("page:2;") for item in chunks)
    assert all("paragraph:" in item["locator"] and "chars:" in item["locator"] for item in chunks)


def test_corrupt_pdf_is_failed_not_dropped(tmp_path):
    store = Store(tmp_path / "docs.sqlite")
    project = store.create_project("资料", "")
    document = ingest_bytes(store, settings(), project["id"], "broken.pdf", b"%PDF-1.4 not-a-real-pdf")
    assert document["status"] == "failed"
    assert document["error"]
    assert store.chunks_for_document(document["id"]) == []
    assert search_project_documents(store, settings(), project["id"], "定价") == []
    store.close()


def test_hybrid_search_rrf_dedupe_and_project_isolation(tmp_path):
    store = Store(tmp_path / "hybrid.sqlite")
    alpha = store.create_project("甲", "")
    beta = store.create_project("乙", "")
    ingest_bytes(store, settings(), alpha["id"], "pricing.pdf", make_pdf(["甲项目 InkBot 定价 20 美元，见产品手册。", "甲项目部署为私有化。"]))
    ingest_bytes(store, settings(), beta["id"], "other.pdf", make_pdf(["乙项目完全不同的主题：园艺与土壤。"]))
    hits = search_project_documents(store, settings(), alpha["id"], "InkBot 定价")
    assert hits
    assert all(item["url"].startswith(f"project://") for item in hits)
    assert any(item.get("page") == 1 and "20 美元" in item["quote"] for item in hits)
    assert all("page:" in item["locator"] for item in hits)
    leaked = search_project_documents(store, settings(), alpha["id"], "园艺与土壤")
    assert not any("园艺" in item["quote"] for item in leaked)
    beta_hits = search_project_documents(store, settings(), beta["id"], "园艺")
    assert beta_hits and all("乙项目" in item["quote"] or "园艺" in item["quote"] for item in beta_hits)
    fused = rrf_fuse([["b", "a", "c"], ["b", "d", "a"]])
    assert fused[0] == "b"
    assert set(fused) == {"a", "b", "c", "d"}
    store.close()


def test_only_ready_documents_are_searchable(tmp_path):
    store = Store(tmp_path / "ready.sqlite")
    project = store.create_project("过滤", "")
    ingest_bytes(store, settings(), project["id"], "ok.md", "可检索的定价说明".encode())
    failed = ingest_bytes(store, settings(), project["id"], "bad.pdf", b"pdf")
    assert failed["status"] == "failed"
    hits = search_project_documents(store, settings(), project["id"], "定价")
    assert hits
    assert all("可检索" in item["quote"] or "定价" in item["quote"] for item in hits)
    store.close()


def test_reindex_on_embedding_model_change(tmp_path):
    store = Store(tmp_path / "reindex.sqlite")
    project = store.create_project("重建", "")
    ingest_bytes(store, settings(), project["id"], "note.md", "混合检索需要记录 embedding_model。".encode())
    first = store.chunks_for_document(store.list_documents(project["id"])[0]["id"])
    assert first and first[0]["embedding_model"] == LocalHashEmbedder.name
    assert first[0]["embedding_dim"] == 64
    store.conn.execute("UPDATE document_chunks SET embedding_model=?, index_version=?", ("old-model", "v0"))
    store.conn.commit()
    hits = search_project_documents(store, settings(), project["id"], "embedding_model")
    assert hits
    refreshed = store.chunks_for_document(store.list_documents(project["id"])[0]["id"])
    assert refreshed[0]["embedding_model"] == LocalHashEmbedder.name
    assert refreshed[0]["index_version"] == "v1"
    assert refreshed[0]["embedding"] != first[0]["embedding"] or refreshed[0]["embedding_model"] != "old-model"
    store.close()


def test_local_embedder_is_deterministic_and_ignores_fetch_keys():
    embedder = get_embedder(settings())
    left = embedder.embed(["同一段原文"])[0]
    right = embedder.embed(["同一段原文"])[0]
    assert left == right
    assert embedder.name == "local-hash-v1"


def test_mcp_tools_inject_project_and_read_chunk(tmp_path):
    store = Store(tmp_path / "mcp.sqlite")
    project = store.create_project("工具", "", owner_id=None)
    ingest_bytes(store, settings(), project["id"], "paper.pdf", make_pdf(["第 1 页讲定价口径。", "第 2 页讲部署边界。"]))
    server = ProjectToolServer(store, settings(), project["id"])
    results = server.call("search_project", {"query": "部署边界"})["results"]
    assert results
    chunk_id = results[0]["chunk_id"]
    evidence = server.call("read_evidence", {"chunk_id": chunk_id})["evidence"]
    assert evidence["locator"].startswith("page:")
    assert evidence["page"] in {1, 2}
    store.close()


def test_empty_text_file_fails_explicitly(tmp_path):
    store = Store(tmp_path / "empty.sqlite")
    project = store.create_project("空", "")
    document = ingest_bytes(store, settings(), project["id"], "blank.txt", b"   \n")
    assert document["status"] == "failed"
    store.close()


def test_content_hash_dedupes_identical_chunks():
    fused = rrf_fuse([["one", "two"], ["two", "one"]])
    assert len(fused) == 2
    assert hashlib.sha256(b"x").hexdigest()


async def test_api_pdf_upload_and_search(tmp_path):
    app = create_app(settings(data_dir=str(tmp_path)))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/api/auth/register", json={"username": "pdf_user", "display_name": "PDF", "password": "test-password-123"})
            project = (await client.post("/api/projects", json={"name": "论文库"})).json()
            uploaded = await client.post(
                f"/api/projects/{project['id']}/documents",
                files={"file": ("paper.pdf", make_pdf(["研究报告第 1 页：定价 20 美元。", "第 2 页：私有化部署。"]), "application/pdf")},
            )
            assert uploaded.status_code == 201
            body = uploaded.json()
            assert body["status"] == "ready"
            assert body["page_count"] == 2
            hits = (await client.get(f"/api/projects/{project['id']}/documents/search", params={"q": "私有化部署"})).json()
            assert hits and hits[0]["page"] == 2
            tools = (await client.get(f"/api/projects/{project['id']}/tools")).json()
            assert {item["name"] for item in tools} == {"search_project", "read_evidence"}


def test_parse_error_type():
    try:
        raise ParseError("PDF 无法打开或已损坏")
    except ValueError as exc:
        assert "损坏" in str(exc)
