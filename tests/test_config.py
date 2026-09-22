from pathlib import Path

import pytest
from pydantic import ValidationError

from gate.config import DEV_SECRET_KEY, Settings
from gate.main import create_app

REAL_KEY = "k" * 43


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    """No SECRET_KEY or DEV in the environment, and no .env file to read."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DEV", raising=False)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


def test_app_refuses_to_start_without_secret_key(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY must be set"):
        create_app()


@pytest.mark.parametrize("dev", ["false", "0", ""])
def test_dev_flag_off_still_requires_secret_key(
    clean_env: pytest.MonkeyPatch, dev: str
) -> None:
    clean_env.setenv("DEV", dev)

    with pytest.raises(ValidationError, match="SECRET_KEY must be set"):
        create_app()


def test_empty_secret_key_counts_as_missing(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SECRET_KEY", "")

    with pytest.raises(ValidationError, match="SECRET_KEY must be set"):
        Settings()


def test_short_secret_key_is_rejected(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SECRET_KEY", "short")

    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings()


def test_secret_key_from_environment(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SECRET_KEY", REAL_KEY)

    assert Settings().secret_key.get_secret_value() == REAL_KEY


def test_secret_key_from_dotenv_file(clean_env: pytest.MonkeyPatch) -> None:
    Path(".env").write_text(f"SECRET_KEY={REAL_KEY}\n")

    assert Settings().secret_key.get_secret_value() == REAL_KEY


@pytest.mark.parametrize("dev", ["true", "1", "True"])
def test_dev_flag_allows_the_fixed_dev_key(
    clean_env: pytest.MonkeyPatch, dev: str
) -> None:
    clean_env.setenv("DEV", dev)

    settings = Settings()

    assert settings.secret_key.get_secret_value() == DEV_SECRET_KEY
    create_app()  # starts


def test_explicit_secret_key_wins_in_dev(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DEV", "true")
    clean_env.setenv("SECRET_KEY", REAL_KEY)

    assert Settings().secret_key.get_secret_value() == REAL_KEY


def test_secret_key_is_not_revealed(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SECRET_KEY", REAL_KEY)

    assert REAL_KEY not in repr(Settings())
