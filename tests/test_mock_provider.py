import json

import pytest
from fastapi.testclient import TestClient

from gate.mock_provider import create_app
from gate.mock_provider import main as mock_main

URL = "/v1/chat/completions"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_valid_request_returns_expected_shape(client: TestClient) -> None:
    response = client.post(
        URL,
        json={
            "model": "mock-1",
            "messages": [
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "hello there"},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "object", "created", "model", "choices", "usage"}
    assert body["object"] == "chat.completion"
    assert body["model"] == "mock-1"
    assert isinstance(body["created"], int)
    assert body["choices"] == [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hello there"},
            "finish_reason": "stop",
        }
    ]
    assert set(body["usage"]) == {"prompt_tokens", "completion_tokens", "total_tokens"}


def test_echoes_last_user_message(client: TestClient) -> None:
    response = client.post(
        URL,
        json={
            "model": "mock-1",
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "second"},
            ],
        },
    )
    assert response.json()["choices"][0]["message"]["content"] == "second"


def test_missing_messages_returns_422(client: TestClient) -> None:
    response = client.post(URL, json={"model": "mock-1"})
    assert response.status_code == 422


def test_empty_messages_returns_422(client: TestClient) -> None:
    response = client.post(URL, json={"model": "mock-1", "messages": []})
    assert response.status_code == 422


def test_missing_model_returns_422(client: TestClient) -> None:
    response = client.post(URL, json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 422


def test_usage_totals_add_up(client: TestClient) -> None:
    response = client.post(
        URL,
        json={
            "model": "mock-1",
            "messages": [
                {"role": "system", "content": "you are a mock"},
                {"role": "user", "content": "count these four words"},
            ],
        },
    )
    usage = response.json()["usage"]
    assert usage["prompt_tokens"] == 8
    assert usage["completion_tokens"] == 4
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def parse_sse(text: str) -> list[str]:
    assert text.endswith("\n\n")
    events = text.split("\n\n")[:-1]
    assert all(event.startswith("data: ") for event in events)
    return [event.removeprefix("data: ") for event in events]


def stream(client: TestClient, content: str, **extra: object) -> list[str]:
    response = client.post(
        URL,
        json={
            "model": "mock-1",
            "messages": [{"role": "user", "content": content}],
            "stream": True,
            **extra,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    return parse_sse(response.text)


@pytest.fixture
def fast_client() -> TestClient:
    return TestClient(create_app(chunk_delay=0))


def test_stream_chunks_reply_word_by_word(fast_client: TestClient) -> None:
    events = stream(fast_client, "count these  four words")

    assert events[-1] == "[DONE]"
    chunks = [json.loads(event) for event in events[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert len({c["id"] for c in chunks}) == 1
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    deltas = [c["choices"][0]["delta"] for c in chunks[1:-1]]
    assert all(set(delta) == {"content"} for delta in deltas)
    words = [delta["content"] for delta in deltas]
    assert words == ["count ", "these  ", "four ", "words"]
    assert chunks[-1]["choices"][0] == {
        "index": 0,
        "delta": {},
        "finish_reason": "stop",
    }


def test_stream_omits_usage_unless_requested(fast_client: TestClient) -> None:
    events = stream(fast_client, "hello there")

    chunks = [json.loads(event) for event in events[:-1]]
    assert all("usage" not in c for c in chunks)
    assert all(len(c["choices"]) == 1 for c in chunks)


def test_stream_include_usage_adds_final_usage_chunk(fast_client: TestClient) -> None:
    events = stream(fast_client, "hello there", stream_options={"include_usage": True})

    assert events[-1] == "[DONE]"
    *content_chunks, usage_chunk = [json.loads(event) for event in events[:-1]]
    assert all(c["usage"] is None for c in content_chunks)
    assert usage_chunk["choices"] == []
    assert usage_chunk["usage"] == {
        "prompt_tokens": 2,
        "completion_tokens": 2,
        "total_tokens": 4,
    }


def test_stream_waits_configured_delay_between_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(mock_main, "sleep", fake_sleep)
    client = TestClient(create_app(chunk_delay=0.25))

    stream(client, "one two three")

    assert delays == [0.25, 0.25, 0.25]
