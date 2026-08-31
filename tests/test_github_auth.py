from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.router import router, settings


app = FastAPI()
app.include_router(router)


def test_login_uses_public_callback_and_secure_state_cookie(monkeypatch) -> None:
    callback_url = "https://112.186.136.153/auth/github/callback"
    monkeypatch.setattr(settings, "github_client_id", "test-client-id")
    monkeypatch.setattr(settings, "github_redirect_uri", callback_url)

    response = TestClient(app).get("/auth/github/login", follow_redirects=False)

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["client_id"] == ["test-client-id"]
    assert query["redirect_uri"] == [callback_url]
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]


def test_success_page_points_to_docs_and_explains_direct_me_check() -> None:
    response = TestClient(app).get("/auth/success?token=test-jwt")

    assert response.status_code == 200
    assert 'href="/docs"' in response.text
    assert "/auth/me 호출" in response.text
    assert "JWT 토큰만 입력" in response.text
