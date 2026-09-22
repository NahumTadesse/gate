import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated

import anyio
import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from gate.config import Settings
from gate.db import create_engine, create_sessionmaker
from gate.schemas import ProxyRequest
from gate.streaming import (
    SSE_HEADERS,
    SSEUsageParser,
    UpstreamStreamingResponse,
    sse_event,
)

logger = logging.getLogger(__name__)

UPSTREAM_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
JSON_HEADERS = {"content-type": "application/json"}
READINESS_TIMEOUT = 2.0


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


def upstream_error_body(message: str) -> dict[str, dict[str, str]]:
    return {"error": {"message": message, "type": "upstream_error"}}


def upstream_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=upstream_error_body(message))


async def forward(client: httpx.AsyncClient, body: bytes) -> Response:
    try:
        upstream = await client.post(
            CHAT_COMPLETIONS_PATH, content=body, headers=JSON_HEADERS
        )
    except httpx.TimeoutException:
        return upstream_error(504, "Upstream provider timed out")
    except httpx.HTTPError:
        return upstream_error(502, "Upstream provider unavailable")
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


async def forward_stream(
    client: httpx.AsyncClient, body: bytes, request: Request
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
                return Response(
                    content=upstream.content,
                    status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type"),
                )
            chunks = upstream.aiter_bytes()
            first = await anext(chunks, b"")
        except httpx.TimeoutException:
            return upstream_error(504, "Upstream provider timed out")
        except httpx.HTTPError:
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
            request.state.usage = parser.usage

        return UpstreamStreamingResponse(
            relay(),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
            close_upstream=stack.pop_all().aclose,
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

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        if not await database_is_up(request.app.state.db_engine):
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "checks": {"database": "down"}},
            )
        return JSONResponse({"status": "ok", "checks": {"database": "up"}})

    @app.post(CHAT_COMPLETIONS_PATH)
    async def chat_completions(
        payload: ProxyRequest,
        request: Request,
        client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    ) -> Response:
        body = await request.body()
        if payload.stream:
            return await forward_stream(client, body, request)
        return await forward(client, body)

    return app
