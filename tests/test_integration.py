"""The proxy wired to the real mock provider app, in-process via ASGITransport."""

import json
from collections.abc import Iterator

import httpx
import pytest
from conftest import skip_api_key_auth
from fastapi.testclient import TestClient

from gate.config import Settings
from gate.main import create_app
from gate.mock_provider import create_app as create_mock_provider

URL = "/v1/chat/completions"
MESSAGES = [
    {"role": "system", "content": "you are a mock"},
    {"role": "user", "content": "echo these words back"},
]


@pytest.fixture
def client() -> Iterator[TestClient]:
    provider = create_mock_provider(chunk_delay=0)
    app = create_app(
        settings=Settings(upstream_base_url="http://mock-provider"),
        transport=httpx.ASGITransport(app=provider),
    )
    with TestClient(skip_api_key_auth(app)) as client:
        yield client


def test_non_streaming_round_trip(client: TestClient) -> None:
    response = client.post(URL, json={"model": "mock-1", "messages": MESSAGES})

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "mock-1"
    assert body["choices"][0]["message"] == {
        "role": "assistant",
        "content": "echo these words back",
    }
    assert body["usage"] == {
        "prompt_tokens": 8,
        "completion_tokens": 4,
        "total_tokens": 12,
    }


def test_streaming_round_trip(client: TestClient) -> None:
    response = client.post(
        URL,
        json={
            "model": "mock-1",
            "messages": MESSAGES,
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        event.removeprefix("data: ") for event in response.text.split("\n\n") if event
    ]
    assert events[-1] == "[DONE]"
    chunks = [json.loads(event) for event in events[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)

    content = "".join(
        choice["delta"].get("content", "") for c in chunks for choice in c["choices"]
    )
    assert content == "echo these words back"
    assert chunks[-1]["choices"] == []
    assert chunks[-1]["usage"] == {
        "prompt_tokens": 8,
        "completion_tokens": 4,
        "total_tokens": 12,
    }
