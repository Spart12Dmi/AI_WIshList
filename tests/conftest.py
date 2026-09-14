import pytest
from fastapi.testclient import TestClient

from app.auth import _attempts
from app.config import get_settings
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.sqlite3"))
    monkeypatch.setattr(settings, "use_llm_planner", False)
    monkeypatch.setattr(settings, "use_semantic_validation", False)
    monkeypatch.setattr(settings, "use_browser_fallback", False)
    _attempts.clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def account(client):
    result = client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "name": "Tester", "password": "a-strong-test-password"},
    )
    assert result.status_code == 201, result.text
    assert "httponly" in result.headers["set-cookie"].lower()
    assert "samesite=strict" in result.headers["set-cookie"].lower()
    client.headers["X-CSRF-Token"] = result.json()["csrf"]
    return result.json()
