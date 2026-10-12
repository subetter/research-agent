"""Pluggable embedding and rerank. Demo/tests use a deterministic local hasher."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_RE.finditer((text or "").lower()):
        piece = match.group(0)
        if re.search(r"[\u4e00-\u9fff]", piece):
            if len(piece) == 1:
                tokens.append(piece)
            else:
                tokens.extend(piece[i : i + 2] for i in range(len(piece) - 1))
        else:
            tokens.append(piece)
    return tokens


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, documents: list[str]) -> list[float]: ...


class LocalHashEmbedder:
    """Hashed n-gram bag. Same text always yields the same unit vector; no network."""

    name = "local-hash-v1"
    dim = 64

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        if not tokens:
            return vec
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            slot = int.from_bytes(digest[:2], "little") % self.dim
            neighbor = int.from_bytes(digest[2:4], "little") % self.dim
            vec[slot] += 1.0
            vec[neighbor] += 0.35
        norm = math.sqrt(sum(value * value for value in vec))
        if not norm:
            return vec
        return [value / norm for value in vec]


class LocalLexicalReranker:
    """Overlap score used after RRF. Deterministic and key-free."""

    name = "local-lexical-v1"

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        query_tokens = set(tokenize(query))
        scores = []
        for document in documents:
            doc_tokens = tokenize(document)
            if not query_tokens or not doc_tokens:
                scores.append(0.0)
                continue
            overlap = sum(1 for token in doc_tokens if token in query_tokens)
            scores.append(overlap / math.sqrt(len(doc_tokens)))
        return scores


class OpenAICompatEmbedder:
    name: str
    dim: int

    def __init__(self, settings):
        self.settings = settings
        self.name = settings.embedding_model or "openai-compat"
        self.dim = int(settings.embedding_dim or 64)

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        response = httpx.post(
            self.settings.embedding_base_url.rstrip("/") + "/embeddings",
            headers={"Authorization": f"Bearer {self.settings.embedding_api_key}"},
            json={"model": self.name, "input": texts},
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        rows = sorted(payload.get("data") or [], key=lambda item: item.get("index", 0))
        vectors = [row.get("embedding") or [] for row in rows]
        if len(vectors) != len(texts):
            raise ValueError("embedding provider returned unexpected count")
        return vectors


def pack_vector(values: list[float]) -> bytes:
    import array

    return array.array("f", values).tobytes()


def unpack_vector(blob: bytes | None) -> list[float]:
    import array

    if not blob:
        return []
    data = array.array("f")
    data.frombytes(blob)
    return list(data)


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def get_embedder(settings) -> Embedder:
    if getattr(settings, "embedding_api_key", "") and getattr(settings, "embedding_provider", "local") != "local":
        return OpenAICompatEmbedder(settings)
    return LocalHashEmbedder()


def get_reranker(settings) -> Reranker:
    del settings
    return LocalLexicalReranker()
