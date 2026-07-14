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


def make_async_openai_client(
    api_base: str,
    api_key: str,
    *,
    max_retries: int = 2,
    timeout: Any = None,
) -> Any:
    """Return an async OpenAI-compatible client using the app network policy."""
    import httpx
    from openai import AsyncOpenAI

    from app.core.network import httpx_client_kwargs

    client_kwargs = httpx_client_kwargs(api_base)
    if timeout is not None:
        client_kwargs["timeout"] = timeout
    http_client = httpx.AsyncClient(**client_kwargs)
    openai_kwargs: dict[str, Any] = {
        "base_url": api_base or None,
        "api_key": api_key or "not-needed",
        "http_client": http_client,
        "max_retries": max_retries,
    }
    if timeout is not None:
        openai_kwargs["timeout"] = timeout
    return AsyncOpenAI(**openai_kwargs)
