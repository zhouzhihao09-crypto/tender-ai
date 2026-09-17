from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.billing_service import stripe
from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models import BillingEvent, Subscription, Tender, User
from tests.test_pdf_service import make_text_pdf


def register(email: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/auth/register", json={"email": email, "password": "phase13-password"})
    assert response.status_code == 201, response.text
    return client


def configure_stripe(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_example")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_example")
    monkeypatch.setattr(settings, "stripe_price_starter", "price_starter")
    monkeypatch.setattr(settings, "stripe_price_professional", "price_professional")
    monkeypatch.setattr(settings, "stripe_price_business", "price_business")


def test_free_user_has_no_subscription_and_checkout_requires_authentication(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_required", True)
    response = TestClient(app).post("/api/billing/checkout", json={"plan": "starter"})
    assert response.status_code == 401
    client = register("phase13-free@example.com")
    state = client.get("/api/billing/state")
    assert state.status_code == 200
    assert state.json()["plan"] == "free"
    assert state.json()["has_subscription"] is False
    monkeypatch.setattr(settings, "stripe_secret_key", "")
    assert client.post("/api/billing/checkout", json={"plan": "free"}).status_code == 400


def test_checkout_validates_internal_plan_and_reuses_customer(monkeypatch) -> None:
    configure_stripe(monkeypatch)
    client = register("phase13-checkout@example.com")
    customers = []
    sessions = []

    def create_customer(**kwargs):
        customers.append(kwargs)
        return {"id": "cus_phase13"}

    def create_session(**kwargs):
        sessions.append(kwargs)
        return {"id": "cs_phase13", "url": "https://checkout.stripe.test/session"}

    monkeypatch.setattr(stripe.Customer, "create", create_customer)
    monkeypatch.setattr(stripe.checkout.Session, "create", create_session)
    assert client.post("/api/billing/checkout", json={"plan": "professional", "price_id": "price_business"}).status_code == 200
    assert client.post("/api/billing/checkout", json={"plan": "starter"}).status_code == 200
    assert len(customers) == 1
    assert sessions[0]["line_items"] == [{"price": "price_professional", "quantity": 1}]
    assert client.post("/api/billing/checkout", json={"plan": "not-a-plan"}).status_code == 400


def test_webhook_signature_activation_duplicate_and_terminal_fallback(monkeypatch) -> None:
    configure_stripe(monkeypatch)
    client = register("phase13-webhook@example.com")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "phase13-webhook@example.com"))
        user_id = user.id

    subscription_payload = {
        "id": "sub_phase13",
        "customer": "cus_phase13_webhook",
        "status": "active",
        "metadata": {"user_id": str(user_id)},
        "cancel_at_period_end": False,
        "current_period_start": int(datetime.utcnow().timestamp()),
        "current_period_end": int((datetime.utcnow() + timedelta(days=30)).timestamp()),
        "items": {"data": [{"price": {"id": "price_professional"}}]},
    }
    event = {"id": "evt_phase13_1", "type": "customer.subscription.updated", "data": {"object": subscription_payload}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda payload, signature, secret: event)
    activated = client.post("/api/billing/webhook", content=b"signed", headers={"Stripe-Signature": "valid"})
    assert activated.status_code == 200
    assert client.get("/api/billing/state").json()["plan"] == "professional"
    duplicate = client.post("/api/billing/webhook", content=b"signed", headers={"Stripe-Signature": "valid"})
    assert duplicate.json()["duplicate"] is True

    event["id"] = "evt_phase13_2"
    event["type"] = "customer.subscription.updated"
    subscription_payload["status"] = "canceled"
    subscription_payload["cancel_at_period_end"] = True
    subscription_payload["current_period_end"] = int((datetime.utcnow() + timedelta(days=10)).timestamp())
    response = client.post("/api/billing/webhook", content=b"signed", headers={"Stripe-Signature": "valid"})
    assert response.status_code == 200
    state = client.get("/api/billing/state").json()
    assert state["plan"] == "professional"
    assert state["cancel_at_period_end"] is True
    event["id"] = "evt_phase13_3"
    subscription_payload["status"] = "canceled"
    subscription_payload["cancel_at_period_end"] = False
    response = client.post("/api/billing/webhook", content=b"signed", headers={"Stripe-Signature": "valid"})
    assert response.status_code == 200
    assert client.get("/api/billing/state").json()["plan"] == "free"


def test_invalid_signature_and_failed_payment_preserve_data(monkeypatch) -> None:
    configure_stripe(monkeypatch)
    client = register("phase13-security@example.com")
    pdf = make_text_pdf(["Reference: BILL-1. Submission deadline: 30 September 2026."])
    tender = client.post("/api/tenders", files={"file": ("billing.pdf", pdf, "application/pdf")})
    assert tender.status_code == 201
    tender_id = tender.json()["id"]
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda payload, signature, secret: (_ for _ in ()).throw(stripe.error.SignatureVerificationError("bad", "sig")))
    response = client.post("/api/billing/webhook", content=b"bad", headers={"Stripe-Signature": "invalid"})
    assert response.status_code == 400
    assert client.get(f"/api/tenders/{tender_id}").status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(BillingEvent).where(BillingEvent.provider_event_id == "evt_phase13_security")) is None
        assert db.scalar(select(Tender).where(Tender.id == tender_id)) is not None
        assert db.scalar(select(Subscription).where(Subscription.user_id == db.scalar(select(User.id).where(User.email == "phase13-security@example.com")))) is None


def test_billing_state_and_portal_are_user_scoped(monkeypatch) -> None:
    configure_stripe(monkeypatch)
    client_a = register("phase13-isolation-a@example.com")
    client_b = register("phase13-isolation-b@example.com")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "phase13-isolation-a@example.com"))
        user.stripe_customer_id = "cus_isolation_a"
        db.add(Subscription(user_id=user.id, provider_customer_id="cus_isolation_a", provider_subscription_id="sub_isolation_a", provider_price_id="price_professional", plan="professional", status="active"))
        db.commit()
    assert client_b.get("/api/billing/state").json()["has_subscription"] is False
    portal_customers = []
    monkeypatch.setattr(stripe.billing_portal.Session, "create", lambda **kwargs: (portal_customers.append(kwargs) or {"url": "https://billing.stripe.test/portal"}))
    assert client_a.post("/api/billing/portal").json()["url"].endswith("portal")
    assert portal_customers[0]["customer"] == "cus_isolation_a"
    assert client_b.post("/api/billing/portal").status_code == 404
