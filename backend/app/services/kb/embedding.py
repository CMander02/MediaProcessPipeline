"""Embedding service via OpenAI-Compatible API."""

from __future__ import annotations

import logging
from typing import Any

from app.core.model_router import EndpointBinding, resolve_embedding_binding

logger = logging.getLogger(__name__)

_BATCH_SIZE = 32


class EmbeddingService:
    """Singleton embedding service. Calls OpenAI-Compatible /embeddings endpoint."""

    def __init__(self) -> None:
        self._client: Any = None
        self._model: str = ""
        self._api_base: str = ""
        self._api_key: str = ""

    def _get_client(self, binding: EndpointBinding | None = None) -> tuple[Any, str]:
        from app.core.settings import get_runtime_settings
        binding = binding or resolve_embedding_binding(get_runtime_settings())
        if not binding.api_base:
            raise RuntimeError(
                "kb_embedding_api_base is not configured — set it in Settings > 知识库"
            )
        if (
            self._client is None
            or self._api_base != binding.api_base
            or self._api_key != binding.api_key
            or self._model != binding.model
        ):
            from app.services.analysis._openai_client import make_openai_client
            self._client = make_openai_client(binding.api_base, binding.api_key)
            self._model = binding.model
            self._api_base = binding.api_base
            self._api_key = binding.api_key
        return self._client, self._model

    def embed_batch(
        self,
        texts: list[str],
        binding: EndpointBinding | None = None,
    ) -> list[list[float]]:
        """Embed a list of texts. Batches internally into groups of _BATCH_SIZE."""
        if not texts:
            return []
        client, model = self._get_client(binding)
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i: i + _BATCH_SIZE]
            response = client.embeddings.create(input=batch, model=model)
            all_embeddings.extend([item.embedding for item in response.data])
        return all_embeddings

    def embed_one(
        self,
        text: str,
        binding: EndpointBinding | None = None,
    ) -> list[float]:
        result = self.embed_batch([text], binding=binding)
        return result[0] if result else []


_embedding_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
