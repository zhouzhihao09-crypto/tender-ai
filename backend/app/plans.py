"""Centralized plan definitions and entitlement / usage service for Phase 12.

The plan model is intentionally decoupled from any payment provider.  A plan is
an application-level entitlement state that determines how many resources a user
may consume within a usage period.  Phase 13 will later connect a payment
provider -> subscription status -> plan, but the entitlement system works
independently of how a user's plan was assigned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Tender, UsageEvent, User


class Plan:
    """Plan identifiers used in the ``users.plan`` column."""

    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    BUSINESS = "business"

    # Backwards-compatible alias: existing development accounts use "LOCAL".
    LOCAL = "LOCAL"

    @classmethod
    def normalize(cls, value: str) -> str:
        """Return the canonical plan name for *value*.

        ``"LOCAL"`` is treated as ``"free"`` so that legacy development
        accounts continue to work after the Phase 12 default change.
        """
        if value and value.upper() == cls.LOCAL:
            return cls.FREE
        return value.lower() if value else cls.FREE


@dataclass(frozen=True)
class PlanDefinition:
    key: str
    display_name: str
    monthly_price_sgd: int
    tender_limit: int          # new tenders per usage period
    ai_question_limit: int     # AI questions per usage period
    saved_tender_limit: Optional[int]  # total retained tenders (None = unlimited)
    team_member_limit: int

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "monthly_price_sgd": self.monthly_price_sgd,
            "tender_limit": self.tender_limit,
            "ai_question_limit": self.ai_question_limit,
            "saved_tender_limit": self.saved_tender_limit,
            "team_member_limit": self.team_member_limit,
        }


PLAN_DEFINITIONS: dict[str, PlanDefinition] = {
    Plan.FREE: PlanDefinition(
        key="free", display_name="Free", monthly_price_sgd=0,
        tender_limit=2, ai_question_limit=30, saved_tender_limit=3, team_member_limit=1,
    ),
    Plan.STARTER: PlanDefinition(
        key="starter", display_name="Starter", monthly_price_sgd=19,
        tender_limit=10, ai_question_limit=200, saved_tender_limit=15, team_member_limit=1,
    ),
    Plan.PROFESSIONAL: PlanDefinition(
        key="professional", display_name="Professional", monthly_price_sgd=49,
        tender_limit=30, ai_question_limit=750, saved_tender_limit=50, team_member_limit=3,
    ),
    Plan.BUSINESS: PlanDefinition(
        key="business", display_name="Business", monthly_price_sgd=99,
        tender_limit=100, ai_question_limit=2500, saved_tender_limit=None, team_member_limit=10,
    ),
}

PLAN_DISPLAY_ORDER = [Plan.FREE, Plan.STARTER, Plan.PROFESSIONAL, Plan.BUSINESS]


def get_plan_definition(value: str | None) -> PlanDefinition:
    """Return the configured plan for a user, falling back to Free."""
    key = Plan.normalize(value or Plan.FREE)
    return PLAN_DEFINITIONS.get(key, PLAN_DEFINITIONS[Plan.FREE])


# ---------------------------------------------------------------------------
# Usage period and entitlement service
# ---------------------------------------------------------------------------


def current_usage_period() -> tuple[datetime, datetime]:
    """Return ``(start, end)`` of the current calendar-month usage period.

    The end boundary is exclusive so that events created at any second on the
    last day of the month are still counted in the current period.  The
    abstraction is intentionally simple for the calendar-month model; future
    paid subscriptions can replace the start/end pair with a per-user billing
    period without touching downstream enforcement code.
    """
    now = datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = start.replace(day=28) + timedelta(days=4)
    end = (next_month.replace(day=1) + timedelta(days=32)).replace(day=1)
    return start, end


def _count_tenders(db: Session, user: User) -> int:
    """Total number of tenders owned by *user* (the saved-tender count)."""
    from .models import Workspace

    workspace_ids = select(Workspace.id).where(Workspace.user_id == user.id)
    return db.scalar(
        select(func.count())
        .select_from(Tender)
        .where(Tender.workspace_id.in_(workspace_ids))
    )


def _count_team_members(db: Session, user: User) -> int:
    """Current team-member count for *user*.

    Phase 12 only models the *limit*; the actual collaboration system is
    future work.  The owner always counts as one member.  When team
    invitations are added, this query should count active invitations too.
    """
    return 1


def get_current_usage(db: Session, user: User) -> dict:
    """Return a usage + limits dictionary for *user*.

    Usage is scoped to the current calendar-month period and to the user's
    owned workspaces only.
    """
    plan_def = get_plan_definition(user.plan)
    period_start, period_end = current_usage_period()

    events = list(
        db.scalars(
            select(UsageEvent).where(
                UsageEvent.user_id == user.id,
                UsageEvent.created_at >= period_start,
                UsageEvent.created_at < period_end,
            )
        )
    )

    tenders_used = sum(e.quantity for e in events if e.usage_type == "TENDER_UPLOAD")
    ai_questions_used = sum(e.quantity for e in events if e.usage_type == "AI_QUESTION")
    saved_tenders = _count_tenders(db, user)
    team_members = _count_team_members(db, user)

    tender_limit = plan_def.tender_limit
    ai_limit = plan_def.ai_question_limit
    stored_limit = plan_def.saved_tender_limit
    team_limit = plan_def.team_member_limit

    return {
        "plan": Plan.normalize(user.plan or ""),
        "subscription_status": user.subscription_status,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "tenders": {
            "used": tenders_used,
            "limit": tender_limit,
            "remaining": max(0, tender_limit - tenders_used),
        },
        "ai_questions": {
            "used": ai_questions_used,
            "limit": ai_limit,
            "remaining": max(0, ai_limit - ai_questions_used),
        },
        "saved_tenders": {
            "used": saved_tenders,
            "limit": stored_limit,
            "remaining": max(0, stored_limit - saved_tenders) if stored_limit else None,
        },
        "team_members": {
            "used": team_members,
            "limit": team_limit,
            "remaining": max(0, team_limit - team_members),
        },
    }


def _limit_error(user: User, usage_type: str, used: int, limit: int) -> dict:
    """Build a structured error payload for a quota breach."""
    return {
        "error": "usage_limit_reached",
        "usage_type": usage_type,
        "limit": limit,
        "used": used,
        "remaining": 0,
        "plan": Plan.normalize(user.plan or ""),
    }


def check_tender_allowance(db: Session, user: User) -> Optional[dict]:
    """Return a structured limit-error dict if the tender limit is reached.

    ``None`` means the request is allowed; a dict means it must be rejected
    with HTTP 403 and the dict as the JSON body.
    """
    plan_def = get_plan_definition(user.plan)
    period_start, period_end = current_usage_period()

    # Monthly tender allowance check
    events = db.scalars(
        select(UsageEvent).where(
            UsageEvent.user_id == user.id,
            UsageEvent.usage_type == "TENDER_UPLOAD",
            UsageEvent.created_at >= period_start,
            UsageEvent.created_at < period_end,
        )
    )
    used = sum(e.quantity for e in events)
    if used >= plan_def.tender_limit:
        return _limit_error(user, "tender", used, plan_def.tender_limit)

    # Saved-tender (retention) limit check
    if plan_def.saved_tender_limit is not None:
        saved = _count_tenders(db, user)
        if saved >= plan_def.saved_tender_limit:
            return _limit_error(user, "saved_tender", saved, plan_def.saved_tender_limit)

    return None


def check_ai_allowance(db: Session, user: User) -> Optional[dict]:
    """Return a structured limit-error dict if the AI limit is reached.

    ``None`` means the request is allowed; a dict means it must be rejected
    with HTTP 403 and the dict as the JSON body.
    """
    plan_def = get_plan_definition(user.plan)
    period_start, period_end = current_usage_period()

    events = db.scalars(
        select(UsageEvent).where(
            UsageEvent.user_id == user.id,
            UsageEvent.usage_type == "AI_QUESTION",
            UsageEvent.created_at >= period_start,
            UsageEvent.created_at < period_end,
        )
    )
    used = sum(e.quantity for e in events)
    if used >= plan_def.ai_question_limit:
        return _limit_error(user, "ai_question", used, plan_def.ai_question_limit)

    return None

