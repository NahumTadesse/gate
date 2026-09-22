from typing import Any, Literal

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool", "developer"]
    content: str


class StreamOptions(BaseModel):
    include_usage: bool = False


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    stream_options: StreamOptions | None = None


class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Literal["stop", "length", "content_filter"]


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage


class Delta(BaseModel):
    role: Literal["assistant"] | None = None
    content: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # Deltas only carry the fields that changed, e.g. {} on the final chunk.
        return {k: v for k, v in handler(self).items() if v is not None}


class ChunkChoice(BaseModel):
    index: int
    delta: Delta
    finish_reason: Literal["stop", "length", "content_filter"] | None = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChunkChoice]
    usage: Usage | None = None


class ProxyRequest(BaseModel):
    """The fields Gate reads for policy; the raw body is forwarded untouched."""

    model: str
    stream: bool = False
