"""Error body shapes per route group, and the OpenAPI description of them.

None of these need a database: every error here happens before a query.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from conftest import skip_api_key_auth
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gate.auth import get_current_user
from gate.config import Settings
from gate.main import create_app, get_http_client

URL = "/v1/chat/completions"
BODY = {"model": "mock-1", "messages": [{"role": "user", "content": "hi"}]}
OPENAI_ERROR_FIELDS = {"message", "type", "param", "code"}


def assert_openai_error(
    response: httpx.Response, status: int, type: str = "invalid_request_error"
) -> dict[str, Any]:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == OPENAI_ERROR_FIELDS
    assert body["error"]["type"] == type
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    error: dict[str, Any] = body["error"]
    return error


@pytest.fixture
def app() -> FastAPI:
    return create_app(
        settings=Settings(upstream_base_url="http://upstream.test"),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # Unhandled errors become 500 responses instead of failing the test.
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


# --- proxy routes: OpenAI's shape ---


@pytest.mark.parametrize(
    ("body", "param"),
    [
        ({"messages": []}, "model"),
        ({"model": 42}, "model"),
        ({"model": "m", "stream": "sometimes"}, "stream"),
        ([], None),
    ],
    ids=["missing-model", "wrong-type", "bad-stream", "not-an-object"],
)
def test_proxy_validation_error(
    app: FastAPI, client: TestClient, body: object, param: str | None
) -> None:
    skip_api_key_auth(app)

    error = assert_openai_error(client.post(URL, json=body), 422)

    assert error["param"] == param
    assert error["code"] is None


def test_proxy_invalid_json(app: FastAPI, client: TestClient) -> None:
    skip_api_key_auth(app)

    response = client.post(
        URL, content=b"{not json", headers={"content-type": "application/json"}
    )

    assert_openai_error(response, 422)


def test_proxy_missing_key(client: TestClient) -> None:
    error = assert_openai_error(client.post(URL, json=BODY), 401)

    assert error["code"] == "invalid_api_key"
    assert error["param"] is None


def test_proxy_unknown_route(client: TestClient) -> None:
    error = assert_openai_error(client.get("/v1/models/nope"), 404)

    assert error["message"] == "Invalid URL (GET /v1/models/nope)"


def test_proxy_wrong_method(client: TestClient) -> None:
    response = client.get(URL)

    assert_openai_error(response, 405)
    assert response.headers["allow"] == "POST"


def test_proxy_upstream_failure() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    failing = create_app(
        settings=Settings(upstream_base_url="http://upstream.test"),
        transport=httpx.MockTransport(refuse),
    )
    with TestClient(skip_api_key_auth(failing)) as client:
        error = assert_openai_error(client.post(URL, json=BODY), 502, "upstream_error")

    assert error["param"] is error["code"] is None


def test_proxy_unexpected_error(app: FastAPI, client: TestClient) -> None:
    def broken() -> None:
        raise RuntimeError("bug")

    skip_api_key_auth(app)
    app.dependency_overrides[get_http_client] = broken

    error = assert_openai_error(client.post(URL, json=BODY), 500, "server_error")

    assert "bug" not in error["message"]  # internals stay internal


# --- management routes: FastAPI's shape ---


def test_management_http_error(client: TestClient) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_management_validation_error(client: TestClient) -> None:
    response = client.post("/api/v1/auth/register", json={"email": "nope"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert {tuple(item["loc"]) for item in detail} == {
        ("body", "email"),
        ("body", "password"),
    }


def test_management_unknown_route(client: TestClient) -> None:
    response = client.get("/api/v1/nope")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_management_wrong_method(client: TestClient) -> None:
    response = client.delete("/api/v1/me")

    assert response.status_code == 405
    assert response.json() == {"detail": "Method Not Allowed"}


def test_management_unexpected_error(app: FastAPI, client: TestClient) -> None:
    def broken() -> None:
        raise RuntimeError("bug")

    app.dependency_overrides[get_current_user] = broken

    response = client.get("/api/v1/me")

    assert response.status_code == 500
    assert response.text == "Internal Server Error"


@pytest.mark.parametrize("path", ["/v1", "/v10/chat/completions", "/healthz/x"])
def test_paths_outside_the_proxy_keep_fastapis_shape(
    client: TestClient, path: str
) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


# --- OpenAPI ---


def operations(app: FastAPI) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path, method, operation)
        for path, item in app.openapi()["paths"].items()
        for method, operation in item.items()
    ]


def schema_ref(response: dict[str, Any]) -> str | None:
    schema = response.get("content", {}).get("application/json", {}).get("schema")
    if schema is None:
        return None
    ref: str = schema.get("$ref") or schema.get("items", {}).get("$ref", "")
    return ref.rsplit("/", 1)[-1]


def test_every_route_is_tagged_by_group(app: FastAPI) -> None:
    for path, method, operation in operations(app):
        if path.startswith("/v1/"):
            expected = ["proxy"]
        elif path.startswith("/api/v1/"):
            expected = ["management"]
        else:
            expected = ["health"]
        assert operation["tags"] == expected, (method, path)


def test_every_success_response_has_a_schema(app: FastAPI) -> None:
    for path, method, operation in operations(app):
        successes = {
            code: response
            for code, response in operation["responses"].items()
            if code.startswith("2")
        }
        assert len(successes) == 1, (method, path)
        [(code, response)] = successes.items()
        if code != "204":
            assert schema_ref(response), (method, path)


def test_documented_errors_match_the_route_group(app: FastAPI) -> None:
    for path, method, operation in operations(app):
        for code, response in operation["responses"].items():
            if code.startswith("2"):
                continue
            expected: str | None
            if path.startswith("/v1/"):
                expected = "OpenAIError"
            elif path.startswith("/api/v1/"):
                expected = "HTTPValidationError" if code == "422" else "ErrorDetail"
            else:
                expected = schema_ref(response)  # health checks: anything
            assert schema_ref(response) == expected, (method, path, code)


def test_proxy_documents_its_errors_and_stream(app: FastAPI) -> None:
    operation = app.openapi()["paths"][URL]["post"]

    assert {"401", "422", "502", "504"} <= set(operation["responses"])
    assert "text/event-stream" in operation["responses"]["200"]["content"]


def test_org_routes_document_not_found_and_auth(app: FastAPI) -> None:
    for path, method, operation in operations(app):
        if path.startswith("/api/v1/orgs/"):
            assert {"401", "404"} <= set(operation["responses"]), (method, path)
