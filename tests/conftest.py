from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from gate.config import Settings
from gate.main import create_app

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
