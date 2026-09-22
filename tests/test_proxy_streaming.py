import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect
from starlette.types import Message

from gate.config import Settings
from gate.main import create_app
from gate.streaming import SSEUsageParser

URL = "/v1/chat/completions"
BODY = {
    "model": "mock-1",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": True,
}
USAGE = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}

Handler = Callable[[httpx.Request], httpx.Response]


def event(payload: object) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


CHUNKS = [
    event({"choices": [{"index": 0, "delta": {"content": "hello "}}]}),
    event({"choices": [{"index": 0, "delta": {"content": "there"}}]}),
    event({"choices": [], "usage": USAGE}),
    b"data: [DONE]\n\n",
]


class UpstreamStream(httpx.AsyncByteStream):
    """An upstream body we control: yields chunks, then optionally fails or hangs."""

    def __init__(
        self,
        chunks: list[bytes],
        error: Exception | None = None,
        hang: bool = False,
        gate: anyio.Event | None = None,
    ) -> None:
        self.chunks = chunks
        self.error = error
        self.hang = hang
        self.gate = gate
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self.chunks):
            if index == 1 and self.gate is not None:
                await self.gate.wait()
            yield chunk
        if self.error is not None:
            raise self.error
        if self.hang:
            await anyio.sleep_forever()

    async def aclose(self) -> None:
        self.closed = True


def sse_response(stream: httpx.AsyncByteStream) -> httpx.Response:
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=stream
    )


# --- via TestClient (buffers the full body, fine for checking content) ---


def test_relays_stream_unchanged_with_sse_headers(
    make_client: Callable[[Handler], TestClient],
    upstream_requests: list[httpx.Request],
) -> None:
    client = make_client(lambda _: sse_response(UpstreamStream(CHUNKS)))

    response = client.post(URL, json=BODY)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.content == b"".join(CHUNKS)
    assert json.loads(upstream_requests[0].content) == BODY


def test_non_stream_request_is_not_streamed(
    make_client: Callable[[Handler], TestClient],
) -> None:
    client = make_client(lambda _: httpx.Response(200, json={"id": "x"}))

    response = client.post(URL, json={**BODY, "stream": False})

    assert response.json() == {"id": "x"}
    assert "x-accel-buffering" not in response.headers


def test_stream_passes_through_upstream_error_status(
    make_client: Callable[[Handler], TestClient],
) -> None:
    error = {"error": {"message": "rate limited"}}
    client = make_client(lambda _: httpx.Response(429, json=error))

    response = client.post(URL, json=BODY)

    assert response.status_code == 429
    assert response.json() == error


def test_stream_connection_error_returns_502(
    make_client: Callable[[Handler], TestClient],
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(refuse)

    response = client.post(URL, json=BODY)

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "upstream_error"


def test_stream_connect_timeout_returns_504(
    make_client: Callable[[Handler], TestClient],
) -> None:
    def time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    client = make_client(time_out)

    response = client.post(URL, json=BODY)

    assert response.status_code == 504


def test_timeout_before_first_chunk_returns_504(
    make_client: Callable[[Handler], TestClient],
) -> None:
    stream = UpstreamStream([], error=httpx.ReadTimeout("timed out"))
    client = make_client(lambda _: sse_response(stream))

    response = client.post(URL, json=BODY)

    assert response.status_code == 504
    assert response.json()["error"]["type"] == "upstream_error"
    assert stream.closed


def test_error_before_first_chunk_returns_502(
    make_client: Callable[[Handler], TestClient],
) -> None:
    stream = UpstreamStream([], error=httpx.RemoteProtocolError("peer closed"))
    client = make_client(lambda _: sse_response(stream))

    response = client.post(URL, json=BODY)

    assert response.status_code == 502
    assert stream.closed


def test_mid_stream_error_appends_error_event(
    make_client: Callable[[Handler], TestClient],
) -> None:
    stream = UpstreamStream(CHUNKS[:2], error=httpx.ReadTimeout("timed out"))
    client = make_client(lambda _: sse_response(stream))

    response = client.post(URL, json=BODY)

    assert response.status_code == 200
    relayed = b"".join(CHUNKS[:2])
    assert response.content.startswith(relayed)
    error_event = response.content[len(relayed) :]
    assert error_event.startswith(b"data: ") and error_event.endswith(b"\n\n")
    assert json.loads(error_event[len(b"data: ") :])["error"]["type"] == (
        "upstream_error"
    )
    assert b"[DONE]" not in response.content
    assert stream.closed


def test_mid_stream_error_after_partial_event_starts_a_fresh_event(
    make_client: Callable[[Handler], TestClient],
) -> None:
    partial = CHUNKS[0] + b'data: {"choi'
    stream = UpstreamStream([partial], error=httpx.RemoteProtocolError("reset"))
    client = make_client(lambda _: sse_response(stream))

    response = client.post(URL, json=BODY)

    rest = response.content[len(partial) :]
    assert rest.startswith(b"\n\ndata: ")
    assert json.loads(rest.strip().removeprefix(b"data: "))["error"]


# --- driving the ASGI app directly, to observe timing and disconnects ---


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_app(stream: UpstreamStream) -> FastAPI:
    return create_app(
        settings=Settings(upstream_base_url="http://upstream.test"),
        transport=httpx.MockTransport(lambda _: sse_response(stream)),
    )


def http_scope(spec_version: str) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": URL,
        "raw_path": URL.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("test", 1),
        "server": ("test", 80),
        "state": {},
    }


async def call_app(
    app: FastAPI,
    scope: dict[str, Any],
    on_send: Callable[[Message], None],
    disconnected: anyio.Event,
) -> None:
    request_sent = False

    async def receive() -> Message:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {
                "type": "http.request",
                "body": json.dumps(BODY).encode(),
                "more_body": False,
            }
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        on_send(message)

    async with app.router.lifespan_context(app):
        with anyio.fail_after(5):
            await app(scope, receive, send)


def body_chunks(messages: list[Message]) -> list[bytes]:
    return [
        m["body"] for m in messages if m["type"] == "http.response.body" and m["body"]
    ]


@pytest.mark.anyio
async def test_first_chunk_is_sent_before_upstream_finishes() -> None:
    gate = anyio.Event()
    stream = UpstreamStream(CHUNKS, gate=gate)
    done = anyio.Event()
    sent: list[Message] = []
    released_after_first_chunk: list[bool] = []

    def on_send(message: Message) -> None:
        sent.append(message)
        if body_chunks([message]) and not gate.is_set():
            # Upstream is still holding back chunk two, so this can only
            # arrive if the proxy is relaying rather than buffering.
            released_after_first_chunk.append(True)
            gate.set()
        if message["type"] == "http.response.body" and not message["more_body"]:
            done.set()

    await call_app(make_app(stream), http_scope("2.3"), on_send, done)

    assert released_after_first_chunk == [True]
    assert body_chunks(sent) == CHUNKS
    assert stream.closed


@pytest.mark.anyio
async def test_client_disconnect_cancels_upstream() -> None:
    stream = UpstreamStream(CHUNKS[:1], hang=True)
    disconnected = anyio.Event()

    def on_send(message: Message) -> None:
        if body_chunks([message]):
            disconnected.set()

    await call_app(make_app(stream), http_scope("2.3"), on_send, disconnected)

    assert stream.closed


@pytest.mark.anyio
async def test_client_disconnect_detected_on_write_cancels_upstream() -> None:
    # ASGI spec 2.4 servers report disconnects by failing send() instead.
    stream = UpstreamStream(CHUNKS, hang=True)

    def on_send(message: Message) -> None:
        if body_chunks([message]):
            raise OSError("client went away")

    with pytest.raises(ClientDisconnect):
        await call_app(make_app(stream), http_scope("2.4"), on_send, anyio.Event())

    assert stream.closed


@pytest.mark.anyio
async def test_usage_is_recorded_on_request_state() -> None:
    stream = UpstreamStream(CHUNKS)
    scope = http_scope("2.3")
    done = anyio.Event()
    sent: list[Message] = []

    def on_send(message: Message) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and not message["more_body"]:
            done.set()

    await call_app(make_app(stream), scope, on_send, done)

    assert scope["state"]["usage"] == USAGE
    assert b"".join(body_chunks(sent)) == b"".join(CHUNKS)


# --- usage parsing ---


def test_usage_parser_finds_usage_split_across_chunks() -> None:
    parser = SSEUsageParser()
    stream = b"".join(CHUNKS)

    for i in range(0, len(stream), 7):
        parser.feed(stream[i : i + 7])

    assert parser.usage == USAGE
    assert parser.at_event_boundary


def test_usage_parser_handles_crlf_and_ignores_noise() -> None:
    parser = SSEUsageParser()

    parser.feed(b": keep-alive\r\n\r\n")
    parser.feed(b"event: message\r\ndata: not json\r\n\r\n")
    parser.feed(b'data: {"usage": null}\r\n\r\n')
    assert parser.usage is None

    parser.feed(b"data: " + json.dumps({"usage": USAGE}).encode() + b"\r")
    assert not parser.at_event_boundary
    parser.feed(b"\n\r\ndata: [DONE]\r\n\r\n")

    assert parser.usage == USAGE
    assert parser.at_event_boundary
