"""Error bodies per route group.

The proxy under /v1 is called by OpenAI clients, so its errors use OpenAI's
shape: {"error": {"message", "type", "param", "code"}}. The management API
under /api/v1 keeps FastAPI's {"detail": ...}.
"""

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException

PROXY_PREFIX = "/v1/"


class OpenAIErrorBody(BaseModel):
    message: str
    type: str
    param: str | None = None
    code: str | None = None


class OpenAIError(BaseModel):
    """The error envelope OpenAI clients parse."""

    error: OpenAIErrorBody


class ApiKeyError(Exception):
    """A missing or bad API key on a proxy route."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def error_body(
    message: str, type: str, param: str | None = None, code: str | None = None
) -> dict[str, Any]:
    return OpenAIError(
        error=OpenAIErrorBody(message=message, type=type, param=param, code=code)
    ).model_dump()


def openai_error(
    status_code: int,
    message: str,
    type: str,
    param: str | None = None,
    code: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(message, type, param, code),
        headers=headers,
    )


def is_proxy_request(request: Request) -> bool:
    return request.url.path.startswith(PROXY_PREFIX)


def error_type(status_code: int) -> str:
    return "server_error" if status_code >= 500 else "invalid_request_error"


async def handle_http_exception(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, HTTPException)
    if not is_proxy_request(request):
        return await http_exception_handler(request, exc)
    message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    if exc.status_code == 404 and exc.detail == "Not Found":
        # What OpenAI says for an unknown route.
        message = f"Invalid URL ({request.method} {request.url.path})"
    return openai_error(
        exc.status_code,
        message,
        error_type(exc.status_code),
        headers=exc.headers,
    )


def validation_param(loc: tuple[int | str, ...]) -> str | None:
    """The offending field as a dotted path, e.g. messages.0.content."""
    parts = [str(part) for part in loc[1:]] if loc[:1] == ("body",) else []
    return ".".join(parts) or None


async def handle_validation_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    if not is_proxy_request(request):
        return await request_validation_exception_handler(request, exc)
    [first, *_] = exc.errors()
    param = validation_param(tuple(first["loc"]))
    message = first["msg"] if param is None else f"{param}: {first['msg']}"
    return openai_error(422, message, "invalid_request_error", param=param)


async def handle_api_key_error(_: Request, exc: Exception) -> Response:
    assert isinstance(exc, ApiKeyError)
    return openai_error(
        401,
        exc.message,
        "invalid_request_error",
        code="invalid_api_key",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> Response:
    # Starlette logs the exception itself and re-raises it after this response.
    if not is_proxy_request(request):
        return PlainTextResponse("Internal Server Error", status_code=500)
    return openai_error(500, "Internal server error", "server_error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(ApiKeyError, handle_api_key_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
