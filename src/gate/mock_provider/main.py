import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Literal

from anyio import sleep
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from gate.schemas import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    Delta,
    Usage,
)

DEFAULT_CHUNK_DELAY = 0.05


def count_tokens(text: str) -> int:
    return len(text.split())


def split_words(text: str) -> list[str]:
    """Split into words, keeping whitespace attached so the pieces rejoin exactly."""
    return [piece for piece in re.split(r"(?<=\s)(?=\S)", text) if piece]


def sse(chunk: ChatCompletionChunk, include_usage: bool) -> str:
    # OpenAI only sends the usage key when include_usage was requested, and
    # then it is null on every chunk except the final one.
    exclude = None if include_usage else {"usage"}
    return f"data: {chunk.model_dump_json(exclude=exclude)}\n\n"


def create_app(chunk_delay: float = DEFAULT_CHUNK_DELAY) -> FastAPI:
    app = FastAPI()

    async def stream_reply(
        request: ChatCompletionRequest, reply: str, usage: Usage
    ) -> AsyncIterator[str]:
        include_usage = bool(
            request.stream_options and request.stream_options.include_usage
        )
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        def chunk(
            delta: Delta, finish_reason: Literal["stop"] | None = None
        ) -> ChatCompletionChunk:
            return ChatCompletionChunk(
                id=completion_id,
                created=created,
                model=request.model,
                choices=[
                    ChunkChoice(index=0, delta=delta, finish_reason=finish_reason)
                ],
            )

        yield sse(chunk(Delta(role="assistant", content="")), include_usage)
        for word in split_words(reply):
            await sleep(chunk_delay)
            yield sse(chunk(Delta(content=word)), include_usage)
        yield sse(chunk(Delta(), finish_reason="stop"), include_usage)
        if include_usage:
            usage_chunk = ChatCompletionChunk(
                id=completion_id,
                created=created,
                model=request.model,
                choices=[],
                usage=usage,
            )
            yield sse(usage_chunk, include_usage)
        yield "data: [DONE]\n\n"

    @app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
    async def chat_completions(
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse | StreamingResponse:
        reply = next(
            (m.content for m in reversed(request.messages) if m.role == "user"), ""
        )
        prompt_tokens = sum(count_tokens(m.content) for m in request.messages)
        completion_tokens = count_tokens(reply)
        usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        if request.stream:
            return StreamingResponse(
                stream_reply(request, reply, usage),
                media_type="text/event-stream",
                headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
            )
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex}",
            created=int(time.time()),
            model=request.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=reply),
                    finish_reason="stop",
                )
            ],
            usage=usage,
        )

    return app
