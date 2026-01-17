import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.agents.base import ChatAgent
from app.models.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Send a chat message and get a response."""
    agent = ChatAgent(model=request.model)
    response = await agent.chat(request.messages)
    return ChatResponse(message=response)


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """Stream a chat response using Server-Sent Events."""
    agent = ChatAgent(model=request.model)

    async def generate():
        async for token in agent.stream(request.messages):
            yield {"data": json.dumps({"content": token})}
        yield {"data": "[DONE]"}

    return EventSourceResponse(generate())
