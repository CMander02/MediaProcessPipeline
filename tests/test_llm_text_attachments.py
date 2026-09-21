import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.settings import RuntimeSettings  # noqa: E402
from app.services.analysis.llm import LLMService  # noqa: E402


def _settings(monkeypatch, provider, api_base=""):
    rt = RuntimeSettings(**{
        "llm_provider": provider,
        f"{provider}_api_key": "test-key",
        f"{provider}_api_base": api_base,
    })
    monkeypatch.setattr("app.services.analysis.llm.get_runtime_settings", lambda: rt)


def _responses_client(monkeypatch, *, status="completed", text='{"结果":"完成🎬"}'):
    create = AsyncMock(return_value=SimpleNamespace(
        status=status,
        incomplete_details=SimpleNamespace(reason="max_output_tokens") if status == "incomplete"
        else None,
        output_text=text,
    ))
    captured = {}

    class Client:
        responses = SimpleNamespace(create=create)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            captured["closed"] = True

    def factory(api_base, api_key, **kwargs):
        captured.update(api_base=api_base, api_key=api_key, **kwargs)
        return Client()

    monkeypatch.setattr(
        "app.services.analysis._openai_client.make_async_openai_client", factory,
    )
    return create, captured


@pytest.mark.asyncio
@pytest.mark.parametrize("api_base", ["", "https://api.openai.com", "https://api.openai.com/v1/"])
async def test_official_openai_sends_complete_utf8_file_to_responses(monkeypatch, api_base):
    _settings(monkeypatch, "openai", api_base)
    create, captured = _responses_client(monkeypatch)
    chat = AsyncMock(side_effect=AssertionError("Chat Completions should not be used"))
    monkeypatch.setattr("litellm.acompletion", chat)
    prompt = '中文字幕🎬 "引号" & <正文>\n' * 10_000

    result = await LLMService()._call(prompt, system_prompt="输出 JSON", max_retries=5)

    assert result == '{"结果":"完成🎬"}'
    request = create.call_args.kwargs
    assert request["instructions"] == "输出 JSON"
    assert request["store"] is False
    assert request["model"] == "gpt-4o"
    content = request["input"][0]["content"]
    assert content[0]["type"] == "input_file"
    assert content[0]["filename"] == "request.txt"
    prefix, data = content[0]["file_data"].split(",", 1)
    assert prefix == "data:text/plain;base64"
    assert base64.b64decode(data).decode("utf-8") == prompt
    assert "request.txt" in content[1]["text"]
    assert len(content[1]["text"]) < 500
    assert captured["api_base"] == "https://api.openai.com/v1"
    assert captured["max_retries"] == 5
    assert captured["closed"] is True
    chat.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,text,error", [
    ("incomplete", '{"结果":', "max_output_tokens"),
    ("failed", "", "failed"),
    ("completed", "  ", "空正文"),
])
async def test_openai_file_input_rejects_unfinished_results(monkeypatch, status, text, error):
    _settings(monkeypatch, "openai")
    _, captured = _responses_client(monkeypatch, status=status, text=text)
    with pytest.raises(RuntimeError, match=error):
        await LLMService()._call("请求")
    assert captured["closed"] is True


@pytest.mark.asyncio
async def test_anthropic_sends_native_text_document_and_preserves_system_prompt(monkeypatch):
    _settings(monkeypatch, "anthropic")
    create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="# 导图\n- 内容"))],
    ))
    monkeypatch.setattr("litellm.acompletion", create)
    prompt = "完整文字🎬\n" * 10_000

    result = await LLMService()._call(prompt, system_prompt="输出 Markdown", stage="mindmap")

    assert result == "# 导图\n- 内容"
    messages = create.call_args.kwargs["messages"]
    assert messages[0] == {"role": "system", "content": "输出 Markdown"}
    document, instruction = messages[1]["content"]
    assert document == {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": prompt},
        "title": "request.txt",
    }
    assert instruction["type"] == "text"
    assert "request.txt" in instruction["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic", "custom"])
async def test_custom_endpoints_keep_existing_text_protocol(monkeypatch, provider):
    rt = RuntimeSettings(**{
        "llm_provider": provider,
        f"{provider}_api_key": "test-key",
        f"{provider}_api_base": "https://gateway.example/v1",
        **({"custom_model": "custom-model"} if provider == "custom" else {}),
    })
    monkeypatch.setattr("app.services.analysis.llm.get_runtime_settings", lambda: rt)
    create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="完成"))],
    ))
    monkeypatch.setattr("litellm.acompletion", create)

    assert await LLMService()._call("完整原文🎬", system_prompt="格式要求") == "完成"
    assert create.call_args.kwargs["messages"] == [
        {"role": "system", "content": "格式要求"},
        {"role": "user", "content": "完整原文🎬"},
    ]


@pytest.mark.asyncio
async def test_deepseek_keeps_text_input_and_stage_options(monkeypatch):
    _settings(monkeypatch, "deepseek")
    service = LLMService()
    create = AsyncMock(return_value="完成")
    monkeypatch.setattr(service, "_call_deepseek", create)

    assert await service._call("完整原文🎬", stage="polish", system_prompt="格式要求") == "完成"
    create.assert_awaited_once_with(
        "完整原文🎬", stage="polish", max_retries=3, system_prompt="格式要求",
    )
