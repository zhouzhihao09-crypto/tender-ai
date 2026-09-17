from pydantic import BaseModel
from typing import Literal


class PackageItemUpdate(BaseModel):
    included: bool | None = None
    status: Literal["MISSING", "FOUND", "REVIEW_REQUIRED", "READY"] | None = None
    notes: str | None = None
    display_order: int | None = None


class PackageStatusUpdate(BaseModel):
    status: Literal["NOT_STARTED", "ASSEMBLING", "READY_FOR_REVIEW", "REVIEWED", "EXPORTED"]
    review_status: str | None = None


class PackageAddEvidence(BaseModel):
    evidence_document_id: int
    bid_document_id: int | None = None


class ExportRequest(BaseModel):
    allow_incomplete: bool = False