from pydantic import BaseModel


class Message(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    model: str | None = None
    max_tokens: int | None = None
    stream: bool = False


class ChatResponse(BaseModel):
    message: Message
    usage: dict | None = None
