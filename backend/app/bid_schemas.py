from pydantic import BaseModel
from typing import Literal


class BidStatusUpdate(BaseModel):
    workflow_status: Literal["NEW", "UNDER_REVIEW", "BID_DECISION_PENDING", "PREPARING", "READY_FOR_REVIEW", "READY_TO_SUBMIT", "SUBMITTED", "NO_BID"]


class BidDecisionUpdate(BaseModel):
    decision: Literal["BID", "NO_BID", "PENDING"]
    note: str | None = None


class ItemStatusUpdate(BaseModel):
    status: str
    notes: str | None = None


class LinkEvidence(BaseModel):
    evidence_document_id: int


class ClarificationUpdate(BaseModel):
    question: str | None = None
    status: Literal["DRAFT", "READY_TO_SEND", "SENT", "ANSWERED", "CLOSED"] | None = None
    answer: str | None = None


class NoteCreate(BaseModel):
    scope: str = "TENDER"
    target_id: int | None = None
    body: str