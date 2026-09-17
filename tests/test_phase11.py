from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.models import User, UserSession
from sqlalchemy import select


def test_registration_validation_duplicate_and_password_never_returned() -> None:
    client = TestClient(app)
    email = f"phase11-{uuid4().hex}@example.com"
    invalid = client.post("/api/auth/register", json={"email": "not-an-email", "password": "phase11-password"})
    assert invalid.status_code == 400
    short_password = client.post("/api/auth/register", json={"email": "valid@example.com", "password": "short"})
    assert short_password.status_code == 422
    response = client.post("/api/auth/register", json={"email": email, "password": "phase11-password"})
    assert response.status_code == 201
    assert response.json()["token"] is None
    assert "password_hash" not in response.json()["user"]
    duplicate = client.post("/api/auth/register", json={"email": email, "password": "phase11-password"})
    assert duplicate.status_code == 409


def test_login_me_and_logout_invalidate_cookie_session() -> None:
    previous = settings.auth_required
    settings.auth_required = True
    try:
        email = f"phase11-login-{uuid4().hex}@example.com"
        client = TestClient(app)
        client.post("/api/auth/register", json={"email": email, "password": "phase11-password"})
        client.post("/api/auth/logout")
        bad = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
        assert bad.status_code == 401
        good = client.post("/api/auth/login", json={"email": email, "password": "phase11-password"})
        assert good.status_code == 200
        assert good.json()["token"] is None
        assert client.get("/api/auth/me").json()["email"] == email
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/overview").status_code == 401
    finally:
        settings.auth_required = previous


def test_expired_and_invalid_sessions_are_rejected() -> None:
    previous = settings.auth_required
    settings.auth_required = True
    try:
        email = f"phase11-expired-{uuid4().hex}@example.com"
        client = TestClient(app)
        client.post("/api/auth/register", json={"email": email, "password": "phase11-password"})
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == email))
            session = db.scalar(select(UserSession).where(UserSession.user_id == user.id))
            session.expires_at = datetime.utcnow() - timedelta(minutes=1)
            db.commit()
        assert client.get("/api/auth/me").status_code == 401
        invalid = TestClient(app)
        invalid.cookies.set(settings.session_cookie_name, "not-a-session")
        assert invalid.get("/api/auth/me").status_code == 401
    finally:
        settings.auth_required = previous


def test_frontend_auth_gate_and_account_controls_are_wired() -> None:
    html = TestClient(app).get("/").text
    for marker in ("id=\"auth-loading\"", "id=\"auth-gate\"", "id=\"login-form\"", "id=\"register-form\"", "id=\"logout-button\"", "/phase11.css"):
        assert marker in html
