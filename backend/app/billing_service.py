import json
from datetime import datetime
from typing import Any

import stripe
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import BillingEvent, Subscription, User
from .plans import PLAN_DEFINITIONS, Plan

PAID_PLANS = (Plan.STARTER, Plan.PROFESSIONAL, Plan.BUSINESS)
ACTIVE_STATUSES = {"active", "trialing"}
TERMINAL_STATUSES = {"canceled", "unpaid", "incomplete_expired"}
PRICE_FIELDS = {
    Plan.STARTER: "stripe_price_starter",
    Plan.PROFESSIONAL: "stripe_price_professional",
    Plan.BUSINESS: "stripe_price_business",
}


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _nested(source: Any, *keys: str) -> Any:
    current = source
    for key in keys:
        current = _value(current, key)
        if current is None:
            return None
    return current


def configured_price(plan: str) -> str:
    field = PRICE_FIELDS.get(plan)
    price_id = getattr(settings, field, "") if field else ""
    if not price_id:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured for this plan.")
    return price_id


def require_stripe() -> None:
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured.")
    stripe.api_key = settings.stripe_secret_key


def customer_for_user(db: Session, user: User) -> str:
    if user.stripe_customer_id:
        return user.stripe_customer_id
    existing = db.scalar(select(Subscription.provider_customer_id).where(Subscription.user_id == user.id, Subscription.provider == "stripe").order_by(Subscription.updated_at.desc()))
    if existing:
        user.stripe_customer_id = existing
        db.commit()
        return existing
    require_stripe()
    customer = stripe.Customer.create(email=user.email, metadata={"user_id": str(user.id)})
    customer_id = str(_value(customer, "id"))
    user.stripe_customer_id = customer_id
    db.commit()
    return customer_id


def checkout_session(db: Session, user: User, plan: str) -> dict:
    if plan not in PAID_PLANS:
        raise HTTPException(status_code=400, detail="Checkout is available only for paid plans.")
    if user.plan in PAID_PLANS and user.subscription_status in {"ACTIVE", "TRIALING", "PAST_DUE"}:
        raise HTTPException(status_code=409, detail="An existing subscription must be managed through the billing portal.")
    price_id = configured_price(plan)
    require_stripe()
    customer_id = customer_for_user(db, user)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=settings.stripe_success_url,
        cancel_url=settings.stripe_cancel_url,
        client_reference_id=str(user.id),
        metadata={"user_id": str(user.id), "plan": plan},
        subscription_data={"metadata": {"user_id": str(user.id)}},
    )
    return {"id": _value(session, "id"), "url": _value(session, "url"), "status": "pending_confirmation"}


def portal_session(db: Session, user: User) -> dict:
    require_stripe()
    customer_id = user.stripe_customer_id or db.scalar(select(Subscription.provider_customer_id).where(Subscription.user_id == user.id, Subscription.provider == "stripe").order_by(Subscription.updated_at.desc()))
    if not customer_id:
        raise HTTPException(status_code=404, detail="No billing account was found.")
    portal = stripe.billing_portal.Session.create(customer=customer_id, return_url=settings.stripe_portal_return_url)
    return {"url": _value(portal, "url")}


def cancel_subscription(db: Session, user: User) -> Subscription:
    require_stripe()
    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.provider == "stripe").order_by(Subscription.updated_at.desc()))
    if not subscription:
        raise HTTPException(status_code=404, detail="No active subscription was found.")
    remote = stripe.Subscription.modify(subscription.provider_subscription_id, cancel_at_period_end=True)
    apply_subscription(db, remote, user_id=user.id)
    db.commit()
    return db.get(Subscription, subscription.id)


def _plan_for_price(price_id: str | None) -> str | None:
    if not price_id:
        return None
    for plan, field in PRICE_FIELDS.items():
        if getattr(settings, field, "") == price_id:
            return plan
    return None


def _user_for_subscription(db: Session, payload: Any, user_id: int | None = None) -> User | None:
    if user_id:
        return db.get(User, user_id)
    customer_id = _value(payload, "customer")
    existing = db.scalar(select(Subscription).where(Subscription.provider_customer_id == customer_id)) if customer_id else None
    if existing:
        return db.get(User, existing.user_id)
    metadata = _value(payload, "metadata", {}) or {}
    candidate = metadata.get("user_id") if isinstance(metadata, dict) else None
    return db.get(User, int(candidate)) if candidate and str(candidate).isdigit() else None


def apply_subscription(db: Session, payload: Any, user_id: int | None = None) -> Subscription | None:
    customer_id = _value(payload, "customer")
    subscription_id = _value(payload, "id")
    if not customer_id or not subscription_id:
        return None
    user = _user_for_subscription(db, payload, user_id)
    if not user:
        return None
    price_id = _nested(payload, "items", "data")
    price_id = _nested(price_id[0], "price", "id") if price_id else None
    plan = _plan_for_price(price_id)
    status_value = str(_value(payload, "status", "incomplete"))
    existing = db.scalar(select(Subscription).where(Subscription.provider_customer_id == customer_id))
    if not existing:
        existing = db.scalar(select(Subscription).where(Subscription.provider_subscription_id == subscription_id))
    if not existing:
        existing = Subscription(provider_customer_id=customer_id, provider_subscription_id=subscription_id, plan=plan or Plan.FREE, status=status_value, user_id=user.id)
        db.add(existing)
    existing.user_id = user.id
    existing.provider_subscription_id = subscription_id
    existing.provider_price_id = price_id or existing.provider_price_id or ""
    existing.status = status_value
    existing.plan = plan or existing.plan or Plan.FREE
    existing.cancel_at_period_end = bool(_value(payload, "cancel_at_period_end", False))
    existing.canceled_at = datetime.fromtimestamp(_value(payload, "canceled_at")) if _value(payload, "canceled_at") else existing.canceled_at
    existing.current_period_start = datetime.fromtimestamp(_value(payload, "current_period_start")) if _value(payload, "current_period_start") else None
    existing.current_period_end = datetime.fromtimestamp(_value(payload, "current_period_end")) if _value(payload, "current_period_end") else None
    period_end = existing.current_period_end
    period_end_future = period_end is not None and period_end > datetime.utcnow()
    if status_value in ACTIVE_STATUSES:
        user.plan = existing.plan if existing.plan in PAID_PLANS else Plan.FREE
    elif status_value == "canceled" and existing.cancel_at_period_end and period_end_future:
        user.plan = existing.plan if existing.plan in PAID_PLANS else Plan.FREE
    elif status_value in TERMINAL_STATUSES:
        user.plan = Plan.FREE
    elif status_value in {"past_due", "incomplete"} and user.plan not in PAID_PLANS:
        user.plan = Plan.FREE
    user.subscription_status = status_value.upper()
    return existing


def process_event(db: Session, event: Any) -> bool:
    event_id = _value(event, "id")
    event_type = _value(event, "type", "")
    if not event_id:
        raise HTTPException(status_code=400, detail="Stripe event has no ID.")
    if db.scalar(select(BillingEvent).where(BillingEvent.provider == "stripe", BillingEvent.provider_event_id == event_id)):
        return False
    data = _nested(event, "data", "object")
    db.add(BillingEvent(provider="stripe", provider_event_id=event_id, event_type=event_type, payload_metadata=json.dumps({"object": _value(data, "object", None)})[:4000]))
    if event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        apply_subscription(db, data)
    elif event_type in {"invoice.paid", "invoice.payment_failed"}:
        customer_id = _value(data, "customer")
        subscription_id = _value(data, "subscription")
        subscription = db.scalar(select(Subscription).where(Subscription.provider_subscription_id == subscription_id)) if subscription_id else db.scalar(select(Subscription).where(Subscription.provider_customer_id == customer_id))
        if subscription:
            subscription.status = "active" if event_type == "invoice.paid" else "past_due"
            user = db.get(User, subscription.user_id)
            if user:
                user.subscription_status = subscription.status.upper()
    elif event_type == "checkout.session.completed":
        user = _user_for_subscription(db, data)
        if user and _value(data, "customer"):
            existing = db.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.provider == "stripe"))
            if existing:
                existing.provider_customer_id = _value(data, "customer")
    db.commit()
    return True


def verify_and_process_webhook(db: Session, payload: bytes, signature: str | None) -> bool:
    if not settings.stripe_webhook_secret or not signature:
        raise HTTPException(status_code=503, detail="Stripe webhook verification is not configured.")
    try:
        event = stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe webhook signature.") from error
    return process_event(db, event)
