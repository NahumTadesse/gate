import pytest
from fastapi.testclient import TestClient

from gate.mock_provider import create_app

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
