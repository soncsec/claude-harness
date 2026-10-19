from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from notes_api.config import Settings
from notes_api.main import create_app

PASSWORD = "correct horse battery"  # 21 characters: passes the 12-character minimum


def make_client(tmp_path: Path, **overrides) -> TestClient:
    # A low scrypt cost keeps tests fast; production uses the OWASP minimum.
    settings = Settings(database_path=str(tmp_path / "test.db"), scrypt_n=2**10, **overrides)
    return TestClient(create_app(settings))


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return make_client(tmp_path)


@pytest.fixture
def login(client: TestClient) -> Callable[[str], dict[str, str]]:
    """Sign up and log in a user; return their Authorization header."""

    def _login(username: str) -> dict[str, str]:
        assert (
            client.post("/users", json={"username": username, "password": PASSWORD}).status_code
            == 201
        )
        response = client.post("/sessions", json={"username": username, "password": PASSWORD})
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['token']}"}

    return _login
