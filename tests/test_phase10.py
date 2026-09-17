from fastapi.testclient import TestClient

from backend.app.main import app
from tests.test_pdf_service import make_text_pdf

client = TestClient(app)


def _register(email: str) -> TestClient:
    account_client = TestClient(app)
    response = account_client.post("/api/auth/register", json={"email": email, "password": "phase10-password"})
    assert response.status_code == 201, response.text
    assert response.json()["token"] is None
    return account_client


def test_authentication_and_workspace_isolation() -> None:
    client_a = _register("phase10-a@example.com")
    client_b = _register("phase10-b@example.com")
    tender_a = client_a.post("/api/tenders", files={"file": ("a.pdf", make_text_pdf(["Reference: A-1. Submission deadline: 30 September 2026."]), "application/pdf")})
    assert tender_a.status_code == 201, tender_a.text
    tender_id = tender_a.json()["id"]
    assert client_a.get(f"/api/tenders/{tender_id}").status_code == 200
    assert client_b.get(f"/api/tenders/{tender_id}").status_code == 404
    assert client_b.post(f"/api/tenders/{tender_id}/ask", params={"question": "What is the deadline?"}).status_code == 404
    evidence_a = client_a.post("/api/company-evidence", files={"file": ("a-evidence.pdf", make_text_pdf(["Company evidence for account A."]), "application/pdf")})
    assert evidence_a.status_code == 201, evidence_a.text
    evidence_id = evidence_a.json()["id"]
    assert client_a.get("/api/company-evidence").json()
    assert client_b.get("/api/company-evidence").json() == []
    assert client_b.get(f"/api/company-evidence/{evidence_id}/preview").status_code == 404
    assert client_b.get(f"/api/tenders/{tender_id}/bid-package").status_code == 404
    assert client_a.get("/api/auth/me").json()["email"] == "phase10-a@example.com"


def test_usage_events_are_account_scoped() -> None:
    account_client = _register("phase10-usage@example.com")
    response = account_client.post("/api/tenders", files={"file": ("usage.pdf", make_text_pdf(["Reference: U-1. Submission deadline: 30 September 2026."]), "application/pdf")})
    assert response.status_code == 201
    tender_id = response.json()["id"]
    assert account_client.post(f"/api/tenders/{tender_id}/ask", params={"question": "What is the deadline?"}).status_code == 200
    usage = account_client.get("/api/usage")
    assert usage.status_code == 200
    assert usage.json()["totals"]["TENDER_UPLOAD"] >= 1
    assert usage.json()["totals"]["AI_QUESTION"] >= 1


def test_storage_adapter_rejects_path_traversal(tmp_path) -> None:
    from backend.app.storage import LocalFileStorage
    import pytest

    storage = LocalFileStorage(tmp_path / "storage")
    with pytest.raises(ValueError):
        storage.save("../outside.pdf", b"blocked")
