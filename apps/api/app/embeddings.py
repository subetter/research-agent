"""Pluggable embedding and rerank. Demo/tests use a deterministic local hasher."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from typing import Protocol

from .secrets import redact_text

TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")
logger = logging.getLogger(__name__)

SILICONFLOW_DEFAULT_BASE = "https://api.siliconflow.cn/v1"
SILICONFLOW_DEFAULT_EMBED = "BAAI/bge-m3"
SILICONFLOW_DEFAULT_RERANK = "BAAI/bge-reranker-v2-m3"
SILICONFLOW_EMBED_BATCH_MAX = 32
RETRYABLE_STATUS = {429, 503, 504}

# Last observed output width per embedding model. Never invent a dim before a response.
_DIM_CACHE: dict[str, int] = {}
_TEST_TRANSPORT = None


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


class ProviderHTTPError(RuntimeError):
    """HTTP/provider failure without request headers or API keys in the message."""

    def __init__(self, status_code: int, reason: str):
        self.status_code = status_code
        self.reason = reason
        super().__init__(f"provider HTTP {status_code}: {reason}")


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


def _sleep(seconds: float) -> None:
    time.sleep(max(0.0, seconds))


def retry_after_seconds(response, attempt: int, base: float = 0.4, cap: float = 8.0) -> float:
    header = None
    if response is not None:
        header = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if header:
        try:
            return min(cap, max(0.0, float(header)))
        except ValueError:
            pass
    return min(cap, base * (2 ** attempt))


def provider_post(url: str, *, headers: dict, payload: dict, timeout: float, max_retries: int) -> dict:
    """POST JSON. Retries 429/503/504 and transport timeouts. Never logs Authorization."""
    import httpx

    last_error: Exception | None = None
    client_kwargs: dict = {"timeout": timeout}
    if _TEST_TRANSPORT is not None:
        client_kwargs["transport"] = _TEST_TRANSPORT
    with httpx.Client(**client_kwargs) as client:
        for attempt in range(max_retries + 1):
            try:
                response = client.post(url, headers=headers, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = ProviderHTTPError(0, "timeout_or_network")
                if attempt >= max_retries:
                    raise last_error from exc
                logger.warning("embedding/rerank transport error, retry %s", attempt + 1)
                _sleep(retry_after_seconds(None, attempt))
                continue
            if response.status_code in RETRYABLE_STATUS:
                last_error = ProviderHTTPError(response.status_code, "rate_limited_or_unavailable")
                if attempt >= max_retries:
                    raise last_error
                logger.warning("embedding/rerank HTTP %s, retry %s", response.status_code, attempt + 1)
                _sleep(retry_after_seconds(response, attempt))
                continue
            if response.status_code >= 400:
                raise ProviderHTTPError(response.status_code, "request_failed")
            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderHTTPError(response.status_code, "invalid_json") from exc
            if not isinstance(data, dict):
                raise ProviderHTTPError(response.status_code, "invalid_json")
            return data
    raise last_error or ProviderHTTPError(0, "request_failed")


def siliconflow_key(settings) -> str:
    return (getattr(settings, "siliconflow_api_key", "") or getattr(settings, "embedding_api_key", "") or "").strip()


def siliconflow_base(settings) -> str:
    return (getattr(settings, "siliconflow_base_url", "") or SILICONFLOW_DEFAULT_BASE).rstrip("/")


def embedding_remote_ready(settings) -> bool:
    provider = getattr(settings, "embedding_provider", "local")
    if provider == "siliconflow":
        return bool(siliconflow_key(settings))
    if provider == "openai_compatible":
        return bool(getattr(settings, "embedding_api_key", "") or "")
    return False


def rerank_remote_ready(settings) -> bool:
    if not siliconflow_key(settings):
        return False
    provider = getattr(settings, "rerank_provider", "auto")
    if provider == "local":
        return False
    if provider == "siliconflow":
        return True
    return getattr(settings, "embedding_provider", "local") == "siliconflow"


def effective_embedding_model(settings) -> str:
    model = (getattr(settings, "embedding_model", "") or "").strip()
    if getattr(settings, "embedding_provider", "local") == "siliconflow" and siliconflow_key(settings):
        if not model or model == LocalHashEmbedder.name:
            return SILICONFLOW_DEFAULT_EMBED
        return model
    return model or LocalHashEmbedder.name


def effective_rerank_model(settings) -> str:
    model = (getattr(settings, "rerank_model", "") or "").strip()
    return model or SILICONFLOW_DEFAULT_RERANK


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _remember_dim(model: str, values: list[list[float]]) -> int:
    width = next((len(item) for item in values if item), 0)
    if width:
        _DIM_CACHE[model] = width
    return width


class OpenAICompatEmbedder:
    name: str
    dim: int

    def __init__(self, settings):
        self.settings = settings
        self.name = effective_embedding_model(settings)
        self.dim = int(_DIM_CACHE.get(self.name) or 0)
        self.base_url = (getattr(settings, "embedding_base_url", "") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = getattr(settings, "embedding_api_key", "") or ""
        self.timeout = float(getattr(settings, "embedding_timeout_seconds", 45) or 45)
        self.retries = int(getattr(settings, "embedding_max_retries", 3) or 0)
        self.batch_size = min(SILICONFLOW_EMBED_BATCH_MAX, int(getattr(settings, "embedding_batch_size", 32) or 32))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return _embed_openai_compatible(
            texts,
            url=self.base_url + "/embeddings",
            api_key=self.api_key,
            model=self.name,
            timeout=self.timeout,
            retries=self.retries,
            batch_size=self.batch_size,
            remember=self,
        )


class SiliconFlowEmbedder:
    """POST /v1/embeddings. Docs: model + input (≤32 strings) + encoding_format=float. No dimensions for bge-m3."""

    name: str
    dim: int

    def __init__(self, settings):
        self.settings = settings
        self.name = effective_embedding_model(settings)
        self.dim = int(_DIM_CACHE.get(self.name) or 0)
        self.base_url = siliconflow_base(settings)
        self.api_key = siliconflow_key(settings)
        self.timeout = float(getattr(settings, "embedding_timeout_seconds", 45) or 45)
        self.retries = int(getattr(settings, "embedding_max_retries", 3) or 0)
        self.batch_size = min(SILICONFLOW_EMBED_BATCH_MAX, int(getattr(settings, "embedding_batch_size", 32) or 32))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return _embed_openai_compatible(
            texts,
            url=self.base_url + "/embeddings",
            api_key=self.api_key,
            model=self.name,
            timeout=self.timeout,
            retries=self.retries,
            batch_size=self.batch_size,
            remember=self,
            encoding_format="float",
        )


def _embed_openai_compatible(
    texts: list[str],
    *,
    url: str,
    api_key: str,
    model: str,
    timeout: float,
    retries: int,
    batch_size: int,
    remember,
    encoding_format: str | None = None,
) -> list[list[float]]:
    if not texts:
        return []
    headers = _auth_headers(api_key)
    out: list[list[float] | None] = [None] * len(texts)
    pending = [index for index, text in enumerate(texts) if (text or "").strip()]
    for start in range(0, len(pending), batch_size):
        group = pending[start : start + batch_size]
        payload: dict = {"model": model, "input": [texts[index] for index in group]}
        if encoding_format:
            payload["encoding_format"] = encoding_format
        data = provider_post(url, headers=headers, payload=payload, timeout=timeout, max_retries=retries)
        rows = sorted(data.get("data") or [], key=lambda item: item.get("index", 0))
        if len(rows) != len(group):
            raise ProviderHTTPError(200, "unexpected_count")
        for offset, row in enumerate(rows):
            vector = row.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise ProviderHTTPError(200, "invalid_embedding")
            out[group[offset]] = [float(value) for value in vector]
    width = _remember_dim(model, [item for item in out if item])
    if width:
        remember.dim = width
    fallback = [0.0] * (remember.dim or width or 0)
    return [item if item is not None else list(fallback) for item in out]


class SiliconFlowReranker:
    """POST /v1/rerank. Docs: model, query, documents; optional top_n. Parse results[].index + relevance_score."""

    name: str

    def __init__(self, settings):
        self.settings = settings
        self.name = effective_rerank_model(settings)
        self.base_url = siliconflow_base(settings)
        self.api_key = siliconflow_key(settings)
        self.timeout = float(getattr(settings, "rerank_timeout_seconds", 45) or 45)
        self.retries = int(getattr(settings, "embedding_max_retries", 3) or 0)

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        if not (query or "").strip():
            return LocalLexicalReranker().rerank(query, documents)
        try:
            payload = {
                "model": self.name,
                "query": query,
                "documents": list(documents),
                "top_n": len(documents),
            }
            data = provider_post(
                self.base_url + "/rerank",
                headers=_auth_headers(self.api_key),
                payload=payload,
                timeout=self.timeout,
                max_retries=self.retries,
            )
            results = data.get("results")
            if not isinstance(results, list):
                raise ProviderHTTPError(200, "invalid_rerank")
            scores = [0.0] * len(documents)
            seen = False
            for item in results:
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                if isinstance(index, int) and 0 <= index < len(documents):
                    scores[index] = float(item.get("relevance_score") or 0.0)
                    seen = True
            if not seen:
                raise ProviderHTTPError(200, "invalid_rerank")
            return scores
        except Exception as exc:
            logger.warning(
                "siliconflow rerank failed (%s); falling back to lexical",
                redact_text(type(exc).__name__),
            )
            return LocalLexicalReranker().rerank(query, documents)


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
    if not embedding_remote_ready(settings):
        return LocalHashEmbedder()
    if getattr(settings, "embedding_provider", "local") == "siliconflow":
        return SiliconFlowEmbedder(settings)
    return OpenAICompatEmbedder(settings)


def get_reranker(settings) -> Reranker:
    if rerank_remote_ready(settings):
        return SiliconFlowReranker(settings)
    return LocalLexicalReranker()


def retrieval_status(settings) -> dict:
    embedder = get_embedder(settings)
    reranker = get_reranker(settings)
    fallback = getattr(settings, "embedding_provider", "local") != "local" and not embedding_remote_ready(settings)
    return {
        "provider": getattr(settings, "embedding_provider", "local"),
        "embedder": embedder.name,
        "reranker": reranker.name,
        "embedding_dim": embedder.dim or None,
        "index_version": getattr(settings, "index_version", "v1"),
        "fallback": fallback,
        "mcp_enabled": bool(getattr(settings, "mcp_enabled", False)),
    }
