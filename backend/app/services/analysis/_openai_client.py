"""Shared OpenAI-compatible client factory for VLM and embedding services."""

from __future__ import annotations

from typing import Any


def make_openai_client(api_base: str, api_key: str) -> Any:
    """Return an openai.OpenAI client pointed at an OpenAI-compatible endpoint."""
    from openai import OpenAI

    return OpenAI(
        base_url=api_base or None,
        api_key=api_key or "not-needed",
    )
