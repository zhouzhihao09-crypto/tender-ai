from datetime import datetime, timedelta

from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.models import UsageEvent, User
from backend.app.plans import PLAN_DEFINITIONS, Plan
from tests.test_pdf_service import make_text_pdf
from fastapi.testclient import TestClient


def register(email: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/auth/register", json={"email": email, "password": "phase12-password"})
    assert response.status_code == 201, response.text
    return client


def test_plan_catalog_and_free_default() -> None:
    client = register("phase12-plan@example.com")
    assert client.get("/api/plan").json()["plan"] == Plan.FREE
    assert {item["key"] for item in client.get("/api/plans").json()["plans"]} == set(PLAN_DEFINITIONS)
    assert PLAN_DEFINITIONS[Plan.FREE].tender_limit == 2
    assert PLAN_DEFINITIONS[Plan.BUSINESS].saved_tender_limit is None


def test_usage_payload_is_period_scoped_and_isolated() -> None:
    client_a = register("phase12-usage-a@example.com")
    client_b = register("phase12-usage-b@example.com")
    response = client_a.get("/api/usage")
    assert response.json()["tenders"]["used"] == 0
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "phase12-usage-a@example.com").one()
        db.add(UsageEvent(user_id=user.id, action="old", usage_type="TENDER_UPLOAD", created_at=datetime.utcnow() - timedelta(days=40)))
        db.commit()
    assert client_a.get("/api/usage").json()["tenders"]["used"] == 0
    assert client_b.get("/api/usage").json()["tenders"]["used"] == 0


def test_free_tender_boundary_is_enforced_server_side() -> None:
    client = register("phase12-tenders@example.com")
    pdf = make_text_pdf(["Reference: P-1. Submission deadline: 30 September 2026."])
    assert client.post("/api/tenders", files={"file": ("one.pdf", pdf, "application/pdf")}).status_code == 201
    assert client.post("/api/tenders", files={"file": ("two.pdf", pdf, "application/pdf")}).status_code == 201
    denied = client.post("/api/tenders", files={"file": ("three.pdf", pdf, "application/pdf")})
    assert denied.status_code == 403
    assert denied.json()["error"] == "usage_limit_reached"
    assert denied.json()["usage_type"] == "tender"


def test_failed_tender_processing_does_not_consume_usage() -> None:
    client = register("phase12-failed-tender@example.com")
    response = client.post("/api/tenders", files={"file": ("empty.pdf", b"%PDF-1.4", "application/pdf")})
    assert response.status_code == 201
    assert client.get("/api/usage").json()["tenders"]["used"] == 0


def test_ai_limit_rejects_direct_api_call_without_consuming_failed_request() -> None:
    client = register("phase12-ai@example.com")
    pdf = make_text_pdf(["Reference: AI-1. Submission deadline: 30 September 2026."])
    tender = client.post("/api/tenders", files={"file": ("ai.pdf", pdf, "application/pdf")})
    assert tender.status_code == 201
    tender_id = tender.json()["id"]
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "phase12-ai@example.com").one()
        db.add_all([UsageEvent(user_id=user.id, action="seed", usage_type="AI_QUESTION", quantity=30) for _ in [0]])
        db.commit()
    response = client.post(f"/api/tenders/{tender_id}/ask", params={"question": "What is required?"})
    assert response.status_code == 403
    assert response.json()["usage_type"] == "ai_question"
    assert client.get("/api/usage").json()["ai_questions"]["used"] == 30