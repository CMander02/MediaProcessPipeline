import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.model_router import resolve_provider_model_binding, resolve_service_model_binding  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402


def test_service_model_binding_routes_by_model_type_on_same_connection():
    settings = RuntimeSettings(
        service_connections=[
            {
                "id": "siliconflow-asr",
                "name": "硅基流动",
                "service_scope": "api",
                "provider": "siliconflow",
                "endpoint_type": "openai_compatible",
                "api_base": "https://api.siliconflow.cn/v1",
                "api_key": "sk-sf",
                "headers": {},
                "enabled": True,
                "timeout_sec": 120,
                "max_concurrency": 4,
            }
        ],
        service_models=[
            {
                "id": "siliconflow-asr:llm",
                "connection_id": "siliconflow-asr",
                "model_id": "Qwen/Qwen3.5-8B",
                "display_name": "Qwen/Qwen3.5-8B",
                "model_type": "llm",
                "capabilities": ["chat"],
                "enabled": True,
            },
            {
                "id": "siliconflow-asr:vlm",
                "connection_id": "siliconflow-asr",
                "model_id": "Pro/deepseek-ai/DeepSeek-V3.2",
                "display_name": "Pro/deepseek-ai/DeepSeek-V3.2",
                "model_type": "vlm",
                "capabilities": ["chat", "vision"],
                "enabled": True,
            },
            {
                "id": "siliconflow-asr:embedding",
                "connection_id": "siliconflow-asr",
                "model_id": "BAAI/bge-m3",
                "display_name": "BAAI/bge-m3",
                "model_type": "embedding",
                "capabilities": ["embedding"],
                "enabled": True,
            },
            {
                "id": "siliconflow-asr:rerank",
                "connection_id": "siliconflow-asr",
                "model_id": "BAAI/bge-reranker-v2-m3",
                "display_name": "BAAI/bge-reranker-v2-m3",
                "model_type": "rerank",
                "capabilities": ["rerank"],
                "enabled": True,
            },
        ],
    )

    llm = resolve_service_model_binding(settings, "siliconflow-asr", "Qwen/Qwen3.5-8B")
    vlm = resolve_service_model_binding(settings, "siliconflow-asr", "Pro/deepseek-ai/DeepSeek-V3.2")
    embedding = resolve_service_model_binding(settings, "siliconflow-asr", "BAAI/bge-m3")
    rerank = resolve_service_model_binding(settings, "siliconflow-asr", "BAAI/bge-reranker-v2-m3")

    assert llm.model_type == "llm"
    assert llm.endpoint == "https://api.siliconflow.cn/v1/chat/completions"
    assert llm.request_kwargs["api_kind"] == "chat"

    assert vlm.model_type == "vlm"
    assert vlm.endpoint == "https://api.siliconflow.cn/v1/chat/completions"
    assert vlm.request_kwargs["api_kind"] == "vision_chat"

    assert embedding.model_type == "embedding"
    assert embedding.endpoint == "https://api.siliconflow.cn/v1/embeddings"
    assert embedding.request_kwargs["api_kind"] == "embedding"

    assert rerank.model_type == "rerank"
    assert rerank.endpoint == "https://api.siliconflow.cn/v1/rerank"
    assert rerank.request_kwargs["api_kind"] == "rerank"


def test_provider_model_binding_routes_by_model_type_on_same_provider():
    settings = RuntimeSettings(
        providers=[
            {
                "id": "siliconflow",
                "name": "SiliconFlow",
                "provider_type": "siliconflow",
                "api_base": "https://api.siliconflow.cn/v1",
                "api_key": "sk-sf",
                "enabled": True,
                "models": [
                    {
                        "id": "siliconflow:Qwen/Qwen3.5-8B",
                        "model_id": "Qwen/Qwen3.5-8B",
                        "model_type": "llm",
                        "capabilities": ["llm", "chat"],
                        "endpoint_path": "/chat/completions",
                        "enabled": True,
                    },
                    {
                        "id": "siliconflow:BAAI/bge-m3",
                        "model_id": "BAAI/bge-m3",
                        "model_type": "embedding",
                        "capabilities": ["embedding"],
                        "endpoint_path": "/embeddings",
                        "enabled": True,
                    },
                    {
                        "id": "siliconflow:BAAI/bge-reranker-v2-m3",
                        "model_id": "BAAI/bge-reranker-v2-m3",
                        "model_type": "rerank",
                        "capabilities": ["rerank"],
                        "endpoint_path": "/rerank",
                        "enabled": True,
                        "default_params": {
                            "request_format": "json",
                            "query_field": "query",
                            "documents_field": "documents",
                            "return_documents": False,
                            "max_chunks_per_doc": 1024,
                        },
                    },
                ],
            }
        ]
    )

    llm = resolve_provider_model_binding(settings, "siliconflow", "Qwen/Qwen3.5-8B", "llm")
    embedding = resolve_provider_model_binding(settings, "siliconflow", "BAAI/bge-m3", "embedding")
    rerank = resolve_provider_model_binding(settings, "siliconflow", "BAAI/bge-reranker-v2-m3", "rerank")

    assert llm.configured is True
    assert llm.endpoint == "https://api.siliconflow.cn/v1/chat/completions"
    assert llm.request_kwargs["api_kind"] == "chat"
    assert embedding.configured is True
    assert embedding.endpoint == "https://api.siliconflow.cn/v1/embeddings"
    assert embedding.request_kwargs["api_kind"] == "embedding"
    assert rerank.configured is True
    assert rerank.endpoint == "https://api.siliconflow.cn/v1/rerank"
    assert rerank.request_kwargs["api_kind"] == "rerank"
    assert rerank.request_kwargs["default_params"]["documents_field"] == "documents"


def test_provider_model_binding_returns_unavailable_when_disabled():
    settings = RuntimeSettings(
        providers=[
            {
                "id": "siliconflow",
                "name": "SiliconFlow",
                "provider_type": "siliconflow",
                "api_base": "https://api.siliconflow.cn/v1",
                "api_key": "sk-sf",
                "enabled": False,
                "models": [
                    {
                        "id": "siliconflow:BAAI/bge-m3",
                        "model_id": "BAAI/bge-m3",
                        "model_type": "embedding",
                        "capabilities": ["embedding"],
                        "endpoint_path": "/embeddings",
                        "enabled": True,
                    }
                ],
            }
        ]
    )

    binding = resolve_provider_model_binding(settings, "siliconflow", "BAAI/bge-m3", "embedding")

    assert binding.enabled is False
    assert binding.configured is False
    assert binding.reason == "provider or model is disabled or incomplete"
