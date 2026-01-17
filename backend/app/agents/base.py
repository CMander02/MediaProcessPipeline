from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from anthropic import Anthropic

from app.core.config import get_settings
from app.models.chat import Message


class BaseAgent(ABC):
    """Base class for all agents."""

    def __init__(self, model: str | None = None):
        settings = get_settings()
        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.model = model or settings.default_model
        self.max_tokens = settings.max_tokens

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Return the system prompt for this agent."""
        pass

    def _format_messages(self, messages: list[Message]) -> list[dict[str, str]]:
        return [{"role": msg.role, "content": msg.content} for msg in messages]

    async def chat(self, messages: list[Message]) -> Message:
        """Send messages and get a response."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system_prompt,
            messages=self._format_messages(messages),
        )
        return Message(
            role="assistant",
            content=response.content[0].text,
        )

    async def stream(self, messages: list[Message]) -> AsyncGenerator[str, None]:
        """Stream responses token by token."""
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system_prompt,
            messages=self._format_messages(messages),
        ) as stream:
            for text in stream.text_stream:
                yield text


class ChatAgent(BaseAgent):
    """A simple chat agent."""

    @property
    def system_prompt(self) -> str:
        return "You are a helpful assistant. Be concise and clear in your responses."
