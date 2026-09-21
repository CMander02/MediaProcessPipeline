import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.model_router import resolve_llm_binding  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.services.analysis.llm import LLMService  # noqa: E402


def _client(monkeypatch, *, content, finish_reason):
    response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content), finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=13790, completion_tokens=2594),
    )
    create = AsyncMock(return_value=response)

    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(
        "app.services.analysis._openai_client.make_async_openai_client",
        lambda *args, **kwargs: Client(),
    )
    monkeypatch.setattr(
        "app.services.analysis.local_llm_runtime.get_local_llm_runtime",
        lambda: SimpleNamespace(ensure=lambda config: "http://localhost:12345"),
    )
    return create


@pytest.mark.asyncio
@pytest.mark.parametrize("content,finish_reason,message", [
    (None, "length", "输出被截断"),
    ('{"tldr": "incomplete', "length", "输出被截断"),
    ("  ", "stop", "返回空正文"),
])
async def test_local_llm_rejects_truncated_and_empty_results(
    monkeypatch, content, finish_reason, message,
):
    _client(monkeypatch, content=content, finish_reason=finish_reason)
    binding = resolve_llm_binding(
        RuntimeSettings(llm_provider="local", local_llm_model_path="model.gguf"),
        stage="summary",
    )
    with pytest.raises(RuntimeError, match=message):
        await LLMService()._call_local_llama_cpp("transcript", binding)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,thinking,expected", [
    ("summary", False, False),
    ("mindmap", False, False),
    ("analyze", True, True),
    ("polish", True, False),
])
async def test_local_llm_applies_thinking_setting(monkeypatch, stage, thinking, expected):
    create = _client(monkeypatch, content='{"tldr":"摘要"}', finish_reason="stop")
    binding = resolve_llm_binding(
        RuntimeSettings(
            llm_provider="local", local_llm_model_path="model.gguf",
            local_llm_thinking=thinking,
        ),
        stage=stage,
    )
    result = await LLMService()._call_local_llama_cpp("transcript", binding)
    assert result == '{"tldr":"摘要"}'
    assert create.call_args.kwargs["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": expected,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [None, {}, {"timeline": []}])
async def test_summary_omits_generated_timestamps_without_source_timeline(monkeypatch, context):
    service = LLMService()
    monkeypatch.setattr(service, "_call", AsyncMock(return_value=(
        '{"tldr":"会议讨论任务设计", "key_facts":["采用统一格式"], '
        '"timeline":[{"start":120,"title":"模型猜测的章节","summary":"内容"}]}'
    )))
    result = await service.summarize("会议转录文本", source_context=context)
    assert result["tldr"] == "会议讨论任务设计"
    assert result["timeline"] == []
