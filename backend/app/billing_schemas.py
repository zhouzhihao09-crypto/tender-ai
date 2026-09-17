from datetime import datetime

from pydantic import BaseModel, Field


class CheckoutRequest(BaseModel):
    plan: str = Field(min_length=1, max_length=40)


class BillingState(BaseModel):
    plan: str
    subscription_status: str
    provider: str | None = None
    cancel_at_period_end: bool = False
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    has_subscription: bool = False