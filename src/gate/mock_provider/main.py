import time
import uuid

from fastapi import FastAPI

from gate.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Usage,
)


def count_tokens(text: str) -> int:
    return len(text.split())


def create_app() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        reply = next(
            (m.content for m in reversed(request.messages) if m.role == "user"), ""
        )
        prompt_tokens = sum(count_tokens(m.content) for m in request.messages)
        completion_tokens = count_tokens(reply)
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
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    return app
