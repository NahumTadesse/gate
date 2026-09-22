from fastapi.testclient import TestClient

from gate.config import Settings
from gate.main import create_app

UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://postgres:x@127.0.0.1:1/gate"


def test_healthz() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_does_not_touch_the_database() -> None:
    app = create_app(settings=Settings(database_url=UNREACHABLE_DATABASE_URL))
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200


def test_readyz_reports_database_down() -> None:
    app = create_app(settings=Settings(database_url=UNREACHABLE_DATABASE_URL))
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "down"}}


def test_readyz_reports_database_up(test_database_url: str) -> None:
    app = create_app(settings=Settings(database_url=test_database_url))
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "up"}}
