"""Embedding service via OpenAI-Compatible API."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_BATCH_SIZE = 32


class EmbeddingService:
    """Singleton embedding service. Calls OpenAI-Compatible /embeddings endpoint."""

    def __init__(self) -> None:
        self._client: Any = None
        self._model: str = ""
        self._api_base: str = ""

    def _get_client(self) -> tuple[Any, str]:
        from app.core.settings import get_runtime_settings
        rt = get_runtime_settings()
        if not rt.kb_embedding_api_base:
            raise RuntimeError(
                "kb_embedding_api_base is not configured — set it in Settings > 知识库"
            )
        if self._client is None or self._api_base != rt.kb_embedding_api_base or self._model != rt.kb_embedding_model:
            from app.services.analysis._openai_client import make_openai_client
            self._client = make_openai_client(rt.kb_embedding_api_base, rt.kb_embedding_api_key)
            self._model = rt.kb_embedding_model
            self._api_base = rt.kb_embedding_api_base
        return self._client, self._model

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts. Batches internally into groups of _BATCH_SIZE."""
        if not texts:
            return []
        client, model = self._get_client()
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i: i + _BATCH_SIZE]
            response = client.embeddings.create(input=batch, model=model)
            all_embeddings.extend([item.embedding for item in response.data])
        return all_embeddings

    def embed_one(self, text: str) -> list[float]:
        result = self.embed_batch([text])
        return result[0] if result else []


_embedding_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
