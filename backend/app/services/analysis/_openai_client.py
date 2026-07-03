"""Shared OpenAI-compatible client factory for VLM and embedding services."""

from __future__ import annotations

from typing import Any


def make_openai_client(api_base: str, api_key: str, *, max_retries: int = 2) -> Any:
    """Return an openai.OpenAI client pointed at an OpenAI-compatible endpoint."""
    import httpx
    from openai import OpenAI

    from app.core.network import httpx_client_kwargs

    http_client = httpx.Client(**httpx_client_kwargs(api_base))
    return OpenAI(
        base_url=api_base or None,
        api_key=api_key or "not-needed",
        http_client=http_client,
        max_retries=max_retries,
    )
