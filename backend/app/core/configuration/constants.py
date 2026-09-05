"""Provider capabilities and legacy field mappings."""

import re
from typing import Any

_CONNECTION_FIELD_FLAT_KEYS: dict[str, dict[str, str]] = {
    "anthropic": {
        "api_base": "anthropic_api_base",
        "api_key": "anthropic_api_key",
    },
    "openai": {
        "api_base": "openai_api_base",
        "api_key": "openai_api_key",
    },
    "deepseek": {
        "api_base": "deepseek_api_base",
        "api_key": "deepseek_api_key",
    },
    "siliconflow-asr": {
        "api_base": "siliconflow_api_base",
        "api_key": "siliconflow_api_key",
    },
    "vision-default": {
        "api_base": "vlm_api_base",
        "api_key": "vlm_api_key",
    },
    "embedding-default": {
        "api_base": "kb_embedding_api_base",
        "api_key": "kb_embedding_api_key",
    },
}

_FLAT_CONNECTION_FIELDS = {
    flat_key: (connection_id, field)
    for connection_id, fields in _CONNECTION_FIELD_FLAT_KEYS.items()
    for field, flat_key in fields.items()
}

_MODEL_TYPE_CAPABILITIES: dict[str, list[str]] = {
    "llm": ["chat"],
    "vlm": ["chat", "vision"],
    "embedding": ["embedding"],
    "rerank": ["rerank"],
    "asr": ["asr"],
}

_MODEL_TYPE_ENDPOINT_PATHS: dict[str, str] = {
    "llm": "/chat/completions",
    "vlm": "/chat/completions",
    "embedding": "/embeddings",
    "rerank": "/rerank",
    "asr": "/audio/transcriptions",
}

_SILICONFLOW_ASR_DEFAULT_PARAMS: dict[str, Any] = {
    "request_format": "multipart",
    "file_field": "file",
    "model_field": "model",
    "include_language": False,
    "max_file_mb": 50,
    "max_duration_sec": 3600,
}

_SILICONFLOW_RERANK_DEFAULT_PARAMS: dict[str, Any] = {
    "request_format": "json",
    "query_field": "query",
    "documents_field": "documents",
    "return_documents": False,
    "max_chunks_per_doc": 1024,
}

_PROVIDER_MODEL_TYPE_CAPABILITIES: dict[str, list[str]] = {
    "llm": ["llm", "chat", "json"],
    "vlm": ["vlm", "chat", "vision", "json"],
    "embedding": ["embedding"],
    "rerank": ["rerank"],
    "asr": ["asr"],
}

_PROVIDER_CONNECTION_ALIASES: dict[str, str] = {
    "siliconflow-asr": "siliconflow",
    "vision-default": "custom-vision-default",
    "embedding-default": "custom-embedding-default",
}

_PROVIDER_FLAT_KEYS: dict[str, dict[str, str]] = {
    "anthropic": {
        "api_base": "anthropic_api_base",
        "api_key": "anthropic_api_key",
        "model": "anthropic_model",
    },
    "openai": {
        "api_base": "openai_api_base",
        "api_key": "openai_api_key",
        "model": "openai_model",
    },
    "deepseek": {
        "api_base": "deepseek_api_base",
        "api_key": "deepseek_api_key",
    },
    "siliconflow": {
        "api_base": "siliconflow_api_base",
        "api_key": "siliconflow_api_key",
        "model": "siliconflow_asr_model",
    },
    "custom-vision-default": {
        "api_base": "vlm_api_base",
        "api_key": "vlm_api_key",
        "model": "vlm_model",
    },
    "custom-embedding-default": {
        "api_base": "kb_embedding_api_base",
        "api_key": "kb_embedding_api_key",
        "model": "kb_embedding_model",
    },
}

_MASKED_SECRET_PATTERN = re.compile(r"^\*{3,}\.{3}.{0,4}$")

_RUNTIME_BINDING_SPECS: dict[str, tuple[str, str, str]] = {
    "polish": ("deepseek", "deepseek_polish_model", "llm"),
    "subtitle_polish": ("deepseek", "deepseek_polish_model", "llm"),
    "subtitle_refine": ("deepseek", "deepseek_polish_model", "llm"),
    "analyze": ("deepseek", "deepseek_analyze_model", "llm"),
    "summary": ("deepseek", "deepseek_summary_model", "llm"),
    "mindmap": ("deepseek", "deepseek_mindmap_model", "llm"),
    "asr": ("sherpa_onnx", "sherpa_model_id", "asr"),
    "vision": ("custom-vision-default", "vlm_model", "vlm"),
    "embedding": ("custom-embedding-default", "kb_embedding_model", "embedding"),
}

_MODEL_FIELD_SPECS: dict[str, tuple[str, str, list[str]]] = {
    "anthropic_model": ("anthropic", "llm", ["chat", "json"]),
    "openai_model": ("openai", "llm", ["chat", "vision", "json"]),
    "deepseek_analyze_model": ("deepseek", "llm", ["chat", "reasoning", "json"]),
    "deepseek_polish_model": ("deepseek", "llm", ["chat", "reasoning", "json"]),
    "deepseek_summary_model": ("deepseek", "llm", ["chat", "reasoning", "json"]),
    "deepseek_mindmap_model": ("deepseek", "llm", ["chat", "reasoning", "json"]),
    "siliconflow_asr_model": ("siliconflow-asr", "asr", ["asr"]),
    "vlm_model": ("vision-default", "vlm", ["chat", "vision", "json"]),
    "kb_embedding_model": ("embedding-default", "embedding", ["embedding"]),
}
