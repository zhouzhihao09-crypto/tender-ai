from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth_service import current_user
from .database import get_db
from .models import BidClarification, BidDocument, BidPackage, BidPackageItem, BidRequirement, BidWorkspace, CompanyEvidenceDocument, Tender, TenderBidAction, TenderChecklistItem, User, Workspace


def _owned_workspace_ids(db: Session, user: User):
    return select(Workspace.id).where(Workspace.user_id == user.id)


def _require_owned_tender(db: Session, user: User, tender_id: int) -> None:
    tender = db.scalar(select(Tender).where(Tender.id == tender_id, Tender.workspace_id.in_(_owned_workspace_ids(db, user))))
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found.")


def _require_owned_evidence(db: Session, user: User, document_id: int) -> None:
    document = db.scalar(select(CompanyEvidenceDocument).where(CompanyEvidenceDocument.id == document_id, CompanyEvidenceDocument.workspace_id.in_(_owned_workspace_ids(db, user))))
    if not document:
        raise HTTPException(status_code=404, detail="Evidence document not found.")


def _require_owned_item(db: Session, user: User, path: str, item_id: int) -> None:
    workspace_ids = _owned_workspace_ids(db, user)
    if "/bid-requirements/" in path:
        owned = db.scalar(select(BidRequirement).join(BidWorkspace, BidRequirement.workspace_id == BidWorkspace.id).join(Tender, BidWorkspace.tender_id == Tender.id).where(BidRequirement.id == item_id, Tender.workspace_id.in_(workspace_ids)))
    elif "/bid-documents/" in path:
        owned = db.scalar(select(BidDocument).join(BidWorkspace, BidDocument.workspace_id == BidWorkspace.id).join(Tender, BidWorkspace.tender_id == Tender.id).where(BidDocument.id == item_id, Tender.workspace_id.in_(workspace_ids)))
    elif "/bid-clarifications/" in path:
        owned = db.scalar(select(BidClarification).join(BidWorkspace, BidClarification.workspace_id == BidWorkspace.id).join(Tender, BidWorkspace.tender_id == Tender.id).where(BidClarification.id == item_id, Tender.workspace_id.in_(workspace_ids)))
    elif "/bid-package-items/" in path:
        owned = db.scalar(select(BidPackageItem).join(BidPackage, BidPackageItem.package_id == BidPackage.id).join(BidWorkspace, BidPackage.workspace_id == BidWorkspace.id).where(BidPackageItem.id == item_id, BidWorkspace.tender_id.in_(select(Tender.id).where(Tender.workspace_id.in_(workspace_ids)))))
    elif "/checklist/" in path:
        owned = db.scalar(select(TenderChecklistItem).where(TenderChecklistItem.id == item_id, TenderChecklistItem.tender_id.in_(select(Tender.id).where(Tender.workspace_id.in_(workspace_ids)))))
    elif "/company-actions/" in path:
        owned = db.scalar(select(TenderBidAction).where(TenderBidAction.id == item_id, TenderBidAction.tender_id.in_(select(Tender.id).where(Tender.workspace_id.in_(workspace_ids)))))
    else:
        owned = None
    if not owned:
        raise HTTPException(status_code=404, detail="Resource not found.")


def enforce_request_ownership(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> None:
    params = request.path_params
    if params.get("tender_id") is not None:
        _require_owned_tender(db, user, int(params["tender_id"]))
    if params.get("document_id") is not None:
        _require_owned_evidence(db, user, int(params["document_id"]))
    if params.get("evidence_document_id") is not None:
        _require_owned_evidence(db, user, int(params["evidence_document_id"]))
    if params.get("bid_document_id") is not None:
        _require_owned_item(db, user, "/bid-documents/", int(params["bid_document_id"]))
    if params.get("item_id") is not None:
        _require_owned_item(db, user, request.url.path, int(params["item_id"]))
    if params.get("action_id") is not None:
        _require_owned_item(db, user, "/company-actions/", int(params["action_id"]))
