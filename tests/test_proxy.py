import json
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from gate.config import Settings
from gate.main import create_app

URL = "/v1/chat/completions"
BODY = {"model": "mock-1", "messages": [{"role": "user", "content": "hi"}]}

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def upstream_requests() -> list[httpx.Request]:
    return []


@pytest.fixture
def make_client(
    upstream_requests: list[httpx.Request],
) -> Iterator[Callable[[Handler], TestClient]]:
    clients: list[TestClient] = []

    def make(handler: Handler) -> TestClient:
        def record(request: httpx.Request) -> httpx.Response:
            upstream_requests.append(request)
            return handler(request)

        app = create_app(
            settings=Settings(upstream_base_url="http://upstream.test"),
            transport=httpx.MockTransport(record),
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def test_forwards_request_and_returns_upstream_body(
    make_client: Callable[[Handler], TestClient],
    upstream_requests: list[httpx.Request],
) -> None:
    upstream_body = {"id": "chatcmpl-123", "object": "chat.completion"}
    client = make_client(lambda _: httpx.Response(200, json=upstream_body))

    response = client.post(URL, json=BODY)

    assert response.status_code == 200
    assert response.json() == upstream_body
    assert len(upstream_requests) == 1
    sent = upstream_requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == "http://upstream.test/v1/chat/completions"
    assert json.loads(sent.content) == BODY


def test_passes_through_upstream_error_status(
    make_client: Callable[[Handler], TestClient],
) -> None:
    error = {"error": {"message": "rate limited"}}
    client = make_client(lambda _: httpx.Response(429, json=error))

    response = client.post(URL, json=BODY)

    assert response.status_code == 429
    assert response.json() == error


def test_forwards_unknown_fields_unchanged(
    make_client: Callable[[Handler], TestClient],
    upstream_requests: list[httpx.Request],
) -> None:
    client = make_client(lambda _: httpx.Response(200, json={}))
    body = {**BODY, "temperature": 0.2, "max_tokens": 64}

    client.post(URL, json=body)

    assert json.loads(upstream_requests[0].content) == body


def test_missing_model_is_not_forwarded(
    make_client: Callable[[Handler], TestClient],
    upstream_requests: list[httpx.Request],
) -> None:
    client = make_client(lambda _: httpx.Response(200, json={}))

    response = client.post(URL, json={"messages": BODY["messages"]})

    assert response.status_code == 422
    assert upstream_requests == []


def test_connection_error_returns_502(
    make_client: Callable[[Handler], TestClient],
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(refuse)

    response = client.post(URL, json=BODY)

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "upstream_error"


def test_timeout_returns_504(
    make_client: Callable[[Handler], TestClient],
) -> None:
    def time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = make_client(time_out)

    response = client.post(URL, json=BODY)

    assert response.status_code == 504
    assert response.json()["error"]["type"] == "upstream_error"


def test_http_client_created_at_startup_with_settings(
    make_client: Callable[[Handler], TestClient],
) -> None:
    client = make_client(lambda _: httpx.Response(200, json={}))
    http_client = client.app.state.http_client  # type: ignore[attr-defined]

    assert isinstance(http_client, httpx.AsyncClient)
    assert str(http_client.base_url) == "http://upstream.test"
    assert http_client.timeout == httpx.Timeout(60.0, connect=5.0)
