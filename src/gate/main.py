import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated, Any, Literal

import anyio
import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from gate.api import router as api_router
from gate.auth import get_api_key
from gate.config import Settings
from gate.db import create_engine, create_sessionmaker
from gate.errors import OpenAIError, error_body, install_error_handlers, openai_error
from gate.models import ApiKey
from gate.reporting import router as reporting_router
from gate.schemas import ChatCompletionResponse, ProxyRequest
from gate.streaming import (
    SSE_HEADERS,
    SSEUsageParser,
    UpstreamStreamingResponse,
    sse_event,
)
from gate.usage import UsageRecorder, get_usage_recorder

logger = logging.getLogger(__name__)

UPSTREAM_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
JSON_HEADERS = {"content-type": "application/json"}
READINESS_TIMEOUT = 2.0

# Records the finished request: its status, the upstream usage object (if
# any), and whether it was relayed as an SSE stream.
Record = Callable[[int, object, bool], Awaitable[None]]


class Health(BaseModel):
    status: Literal["ok"]


class Readiness(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, Literal["up", "down"]]


def proxy_error(description: str) -> dict[str, Any]:
    return {"model": OpenAIError, "description": description}


PROXY_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "model": ChatCompletionResponse,
        "description": "The provider's completion. With `stream: true`, a "
        "text/event-stream of chat.completion.chunk events instead.",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    },
    401: proxy_error("Missing, unknown or revoked API key"),
    422: proxy_error("The body isn't a valid chat completion request"),
    502: proxy_error("The provider couldn't be reached"),
    504: proxy_error("The provider timed out"),
}


def get_http_client(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http_client
    return client


async def database_is_up(engine: AsyncEngine) -> bool:
    try:
        # Bounded so an unreachable host fails the probe instead of hanging it.
        with anyio.fail_after(READINESS_TIMEOUT):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        logger.warning("Database readiness check failed: %r", exc)
        return False
    return True


def upstream_error_body(message: str) -> dict[str, Any]:
    return error_body(message, "upstream_error")


def upstream_error(status_code: int, message: str) -> JSONResponse:
    return openai_error(status_code, message, "upstream_error")


def response_usage(body: bytes) -> object:
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload.get("usage") if isinstance(payload, dict) else None


async def forward(client: httpx.AsyncClient, body: bytes, record: Record) -> Response:
    try:
        upstream = await client.post(
            CHAT_COMPLETIONS_PATH, content=body, headers=JSON_HEADERS
        )
    except httpx.TimeoutException:
        await record(504, None, False)
        return upstream_error(504, "Upstream provider timed out")
    except httpx.HTTPError:
        await record(502, None, False)
        return upstream_error(502, "Upstream provider unavailable")
    await record(upstream.status_code, response_usage(upstream.content), False)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


async def forward_stream(
    client: httpx.AsyncClient, body: bytes, record: Record
) -> Response:
    async with AsyncExitStack() as stack:
        # Everything up to and including the first body chunk can still fail
        # with a proper status code; after that the 200 is already on the wire.
        try:
            upstream = await stack.enter_async_context(
                client.stream(
                    "POST", CHAT_COMPLETIONS_PATH, content=body, headers=JSON_HEADERS
                )
            )
            if not upstream.is_success:
                await upstream.aread()
                await record(
                    upstream.status_code, response_usage(upstream.content), False
                )
                return Response(
                    content=upstream.content,
                    status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type"),
                )
            chunks = upstream.aiter_bytes()
            first = await anext(chunks, b"")
        except httpx.TimeoutException:
            await record(504, None, False)
            return upstream_error(504, "Upstream provider timed out")
        except httpx.HTTPError:
            await record(502, None, False)
            return upstream_error(502, "Upstream provider unavailable")

        parser = SSEUsageParser()

        async def relay() -> AsyncIterator[bytes]:
            parser.feed(first)
            yield first
            try:
                async for chunk in chunks:
                    parser.feed(chunk)
                    yield chunk
            except httpx.HTTPError as exc:
                logger.warning("Upstream stream failed mid-response: %r", exc)
                if not parser.at_event_boundary:
                    yield b"\n\n"
                yield sse_event(upstream_error_body("Upstream stream interrupted"))

        close_upstream = stack.pop_all().aclose

        async def finish() -> None:
            # Runs however the stream ended, including a client disconnect, in
            # which case usage is whatever had arrived (usually none).
            try:
                await close_upstream()
            finally:
                await record(200, parser.usage, True)

        return UpstreamStreamingResponse(
            relay(),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
            on_close=finish,
        )


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            base_url=settings.upstream_base_url,
            timeout=UPSTREAM_TIMEOUT,
            transport=transport,
        ) as client:
            engine = create_engine(settings.database_url)
            app.state.http_client = client
            app.state.db_engine = engine
            app.state.sessionmaker = create_sessionmaker(engine)
            try:
                yield
            finally:
                await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(api_router)
    app.include_router(reporting_router)

    @app.get("/healthz", tags=["health"])
    def healthz() -> Health:
        return Health(status="ok")

    @app.get(
        "/readyz",
        tags=["health"],
        response_model=Readiness,
        responses={503: {"model": Readiness, "description": "A dependency is down"}},
    )
    async def readyz(request: Request) -> JSONResponse:
        if not await database_is_up(request.app.state.db_engine):
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "checks": {"database": "down"}},
            )
        return JSONResponse({"status": "ok", "checks": {"database": "up"}})

    @app.post(CHAT_COMPLETIONS_PATH, tags=["proxy"], responses=PROXY_RESPONSES)
    async def chat_completions(
        payload: ProxyRequest,
        request: Request,
        key: Annotated[ApiKey, Depends(get_api_key)],
        recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
        client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    ) -> Response:
        """OpenAI-compatible chat completions, forwarded to the provider.

        The body is forwarded unchanged; Gate itself only reads `model` and
        `stream`. Errors from the provider are passed through with its status
        and body.
        """
        body = await request.body()

        async def record(status_code: int, usage: object, streamed: bool) -> None:
            await recorder.record(
                key=key,
                model=payload.model,
                status_code=status_code,
                usage=usage,
                streamed=streamed,
            )

        if payload.stream:
            return await forward_stream(client, body, record)
        return await forward(client, body, record)

    return app
