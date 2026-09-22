from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from gate.config import Settings
from gate.schemas import ProxyRequest

UPSTREAM_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


def get_http_client(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http_client
    return client


def upstream_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": "upstream_error"}},
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
            app.state.http_client = client
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/chat/completions")
    async def chat_completions(
        payload: ProxyRequest,
        request: Request,
        client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    ) -> Response:
        try:
            upstream = await client.post(
                "/v1/chat/completions",
                content=await request.body(),
                headers={"content-type": "application/json"},
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

    return app
