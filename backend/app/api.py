from pathlib import Path
from uuid import uuid4

import json
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .rate_limit import rate_limit_dependency
from .queue import enqueue_analysis
from .models import AnalysisStatus, Tender, TenderChecklistItem, TenderDate, TenderDocument, TenderQuestion, TenderRequirement, TenderRisk, TenderSource, TenderStatus
from .analysis_schemas import AskResponse, SourceRead
from .bid_schemas import BidDecisionUpdate, BidStatusUpdate, ClarificationUpdate, ItemStatusUpdate, LinkEvidence, NoteCreate
from .package_schemas import ExportRequest, PackageAddEvidence, PackageItemUpdate, PackageStatusUpdate
from .company_schemas import BidActionStatusUpdate, CompanyAssessmentRead, CompanyProfileRead, CompanyProfileUpdate
from .evidence_schemas import EvidenceDocumentRead, EvidenceMetadataUpdate, EvidenceSearchResult
from .models import BidClarification, BidDocument, BidDocumentLink, BidNote, BidPackage, BidPackageItem, BidRequirement, BidWorkspace, CompanyEvidenceDocument, CompanyEvidenceChunk, CompanyProfile, TenderAssessment, TenderBidAction, TenderRequirementEvidence, TenderRequirementMatch
from .schemas import ChecklistItemRead, ChecklistStatusUpdate, DashboardResponse, RequirementRead, TenderDetail, TenderInsight, TenderListItem
from .services.analysis_service import analyze_tender, answer_question
from .services.company_service import assess_tender, get_or_create_profile, update_profile
from .services.evidence_service import CATEGORIES, search_evidence, store_pdf
from .services.bid_service import get_or_create_workspace, readiness_summary, workspace_payload
from .services.package_service import export_package, get_or_create_package, package_payload
from .services.retrieval_service import retrieve
from .auth_schemas import AuthResponse, LoginRequest, RegisterRequest, UserRead
from .billing_schemas import BillingState, CheckoutRequest
from .billing_service import cancel_subscription, checkout_session, portal_session, process_event, verify_and_process_webhook
from .auth_service import create_session, create_user, current_user, normalize_email, revoke_session, set_session_cookie, verify_password, workspace_for_user
from .authz import enforce_request_ownership
from .models import Subscription, UsageEvent, User, Workspace
from .plans import PLAN_DEFINITIONS, PLAN_DISPLAY_ORDER, Plan, check_ai_allowance, check_tender_allowance, get_current_usage, get_plan_definition
from .storage import document_storage, evidence_storage, export_storage

router = APIRouter(prefix="/api", dependencies=[Depends(enforce_request_ownership), Depends(rate_limit_dependency)])
auth_router = APIRouter(prefix="/api/auth")
billing_router = APIRouter(prefix="/api/billing")
CHECKLIST_STATUSES = {"NOT_STARTED", "IN_PROGRESS", "DONE", "BLOCKED"}


@billing_router.post("/checkout")
def start_checkout(request: CheckoutRequest, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return checkout_session(db, user, request.plan.lower())


@billing_router.get("/state", response_model=BillingState)
def billing_state(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.provider == "stripe").order_by(Subscription.updated_at.desc()))
    return {"plan": Plan.normalize(user.plan), "subscription_status": user.subscription_status, "provider": subscription.provider if subscription else None, "cancel_at_period_end": subscription.cancel_at_period_end if subscription else False, "current_period_start": subscription.current_period_start if subscription else None, "current_period_end": subscription.current_period_end if subscription else None, "has_subscription": subscription is not None}


@billing_router.post("/portal")
def create_billing_portal(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return portal_session(db, user)


@billing_router.post("/cancel", response_model=BillingState)
def cancel_billing(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    cancel_subscription(db, user)
    return billing_state(user, db)


@billing_router.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(None, alias="Stripe-Signature"), db: Session = Depends(get_db)) -> dict:
    processed = verify_and_process_webhook(db, await request.body(), stripe_signature)
    return {"received": True, "duplicate": not processed}


@auth_router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, response: Response, db: Session = Depends(get_db)) -> dict:
    user, workspace = create_user(db, request.email, request.password)
    set_session_cookie(response, create_session(db, user))
    return {"token": None, "user": user, "workspace_id": workspace.id}


@auth_router.post("/login", response_model=AuthResponse)
def login(request: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == normalize_email(request.email)))
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.", headers={"WWW-Authenticate": "Bearer"})
    set_session_cookie(response, create_session(db, user))
    return {"token": None, "user": user, "workspace_id": workspace_for_user(db, user).id}


@auth_router.get("/me", response_model=UserRead)
def me(user: User = Depends(current_user)) -> User:
    return user


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else request.cookies.get(settings.session_cookie_name)
    if token:
        revoke_session(db, token)
    response.delete_cookie(settings.session_cookie_name, path="/")


def _assessment_response(db: Session, assessment: TenderAssessment) -> dict:
    payload = json.loads(assessment.result_json)
    return {"recommendation": assessment.recommendation, "confidence": assessment.confidence, "readiness_score": assessment.readiness_score, "explanation": assessment.explanation, "profile_version": assessment.company_profile_version, "matches": list(db.scalars(select(TenderRequirementMatch).where(TenderRequirementMatch.assessment_id == assessment.id))), "actions": list(db.scalars(select(TenderBidAction).where(TenderBidAction.assessment_id == assessment.id))), "counts": payload.get("counts", {})}


def _workspace(db: Session, tender_id: int) -> BidWorkspace:
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found.")
    return get_or_create_workspace(db, tender)


def _package(db: Session, tender_id: int) -> BidPackage:
    return get_or_create_package(db, _workspace(db, tender_id))


def _record_usage(db: Session, user: User, action: str, usage_type: str, workspace_id: int | None = None, tender_id: int | None = None) -> None:
    db.add(UsageEvent(user_id=user.id, workspace_id=workspace_id, tender_id=tender_id, action=action, usage_type=usage_type))
    db.commit()


@router.get("/tenders/{tender_id}/bid-package")
def get_bid_package(tender_id: int, db: Session = Depends(get_db)) -> dict:
    return package_payload(db, _package(db, tender_id))


@router.patch("/tenders/{tender_id}/bid-package")
def update_bid_package(tender_id: int, update: PackageStatusUpdate, db: Session = Depends(get_db)) -> dict:
    package = _package(db, tender_id)
    package.status = update.status
    if update.review_status is not None:
        package.review_status = update.review_status
    db.commit()
    return package_payload(db, package)


@router.patch("/bid-package-items/{item_id}")
def update_bid_package_item(item_id: int, update: PackageItemUpdate, db: Session = Depends(get_db)) -> dict:
    item = db.get(BidPackageItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Package item not found.")
    for field in ("included", "status", "notes", "display_order"):
        value = getattr(update, field)
        if value is not None:
            setattr(item, field, value)
    db.commit()
    return {"id": item.id, "included": item.included, "status": item.status, "notes": item.notes, "display_order": item.display_order}


@router.post("/tenders/{tender_id}/bid-package/evidence")
def add_package_evidence(tender_id: int, item: PackageAddEvidence, db: Session = Depends(get_db)) -> dict:
    package = _package(db, tender_id)
    if not db.get(CompanyEvidenceDocument, item.evidence_document_id):
        raise HTTPException(status_code=404, detail="Evidence document not found.")
    existing = db.scalar(select(BidPackageItem).where(BidPackageItem.package_id == package.id, BidPackageItem.bid_document_id == item.bid_document_id, BidPackageItem.evidence_document_id == item.evidence_document_id))
    if existing:
        return {"id": existing.id, "status": existing.status}
    package_item = BidPackageItem(package_id=package.id, bid_document_id=item.bid_document_id, evidence_document_id=item.evidence_document_id, display_order=len(list(db.scalars(select(BidPackageItem).where(BidPackageItem.package_id == package.id)))) + 1, status="FOUND")
    db.add(package_item)
    db.commit()
    return {"id": package_item.id, "status": package_item.status}


@router.get("/company-evidence/{document_id}/preview")
def preview_company_evidence(document_id: int, page: int = Query(1, ge=1), db: Session = Depends(get_db)) -> dict:
    document = db.get(CompanyEvidenceDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Evidence document not found.")
    chunks = list(db.scalars(select(CompanyEvidenceChunk).where(CompanyEvidenceChunk.document_id == document_id, CompanyEvidenceChunk.page == page)))
    return {"filename": document.filename, "category": document.category, "page_count": document.page_count, "expiry_date": document.expiry_date, "page": page, "text": "\n".join(chunk.text for chunk in chunks), "description": document.description}


@router.post("/tenders/{tender_id}/bid-package/export")
def export_bid_package(tender_id: int, request: ExportRequest, background_tasks: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)):
    package = _package(db, tender_id)
    try:
        output, payload = export_package(db, package, request.allow_incomplete)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _record_usage(db, user, "export_package", "EXPORT", package.workspace_id, tender_id)
    # The ZIP is streamed from the returned path, so any temporary build
    # artifact must be released only after FastAPI has finished sending the
    # response.  Local storage treats release_path as a no-op, so persistent
    # exports are never affected.
    background_tasks.add_task(export_storage.release_path, output)
    return FileResponse(output, media_type="application/zip", filename=output.name, headers={"X-Package-Readiness": payload["readiness"]["state"]})


@router.get("/tenders/{tender_id}/bid-workspace")
def get_bid_workspace(tender_id: int, db: Session = Depends(get_db)) -> dict:
    return workspace_payload(db, _workspace(db, tender_id))


@router.patch("/tenders/{tender_id}/bid-workspace/status")
def update_bid_status(tender_id: int, update: BidStatusUpdate, db: Session = Depends(get_db)) -> dict:
    workspace = _workspace(db, tender_id)
    workspace.workflow_status = update.workflow_status
    db.commit()
    return workspace_payload(db, workspace)


@router.patch("/tenders/{tender_id}/bid-workspace/decision")
def update_bid_decision(tender_id: int, update: BidDecisionUpdate, db: Session = Depends(get_db)) -> dict:
    workspace = _workspace(db, tender_id)
    workspace.decision = update.decision
    workspace.decision_note = update.note
    db.commit()
    return workspace_payload(db, workspace)


@router.patch("/bid-requirements/{item_id}")
def update_bid_requirement(item_id: int, update: ItemStatusUpdate, db: Session = Depends(get_db)) -> dict:
    if update.status not in {"NOT_STARTED", "IN_PROGRESS", "READY", "MISSING", "BLOCKED", "NOT_APPLICABLE"}:
        raise HTTPException(status_code=400, detail="Invalid submission requirement status.")
    item = db.get(BidRequirement, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Bid requirement not found.")
    item.status, item.notes = update.status, update.notes
    db.commit()
    return {"id": item.id, "status": item.status, "notes": item.notes}


@router.patch("/bid-documents/{item_id}")
def update_bid_document(item_id: int, update: ItemStatusUpdate, db: Session = Depends(get_db)) -> dict:
    if update.status not in {"MISSING", "FOUND", "REVIEW_REQUIRED", "READY"}:
        raise HTTPException(status_code=400, detail="Invalid bid document status.")
    item = db.get(BidDocument, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Bid document not found.")
    item.status, item.notes = update.status, update.notes
    db.commit()
    return {"id": item.id, "status": item.status, "notes": item.notes}


@router.post("/bid-documents/{item_id}/evidence")
def link_bid_document(item_id: int, link: LinkEvidence, db: Session = Depends(get_db)) -> dict:
    item = db.get(BidDocument, item_id)
    if not item or not db.get(CompanyEvidenceDocument, link.evidence_document_id):
        raise HTTPException(status_code=404, detail="Bid or evidence document not found.")
    existing = db.scalar(select(BidDocumentLink).where(BidDocumentLink.bid_document_id == item_id, BidDocumentLink.evidence_document_id == link.evidence_document_id))
    if not existing:
        db.add(BidDocumentLink(bid_document_id=item_id, evidence_document_id=link.evidence_document_id))
        db.commit()
    return {"bid_document_id": item_id, "evidence_document_id": link.evidence_document_id}


@router.patch("/bid-clarifications/{item_id}")
def update_bid_clarification(item_id: int, update: ClarificationUpdate, db: Session = Depends(get_db)) -> dict:
    item = db.get(BidClarification, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Bid clarification not found.")
    if update.question is not None:
        item.question = update.question
    if update.status is not None:
        item.status = update.status
    if update.answer is not None:
        item.answer = update.answer
    db.commit()
    return {"id": item.id, "question": item.question, "status": item.status, "answer": item.answer}


@router.post("/tenders/{tender_id}/bid-notes")
def create_bid_note(tender_id: int, note: NoteCreate, db: Session = Depends(get_db)) -> dict:
    workspace = _workspace(db, tender_id)
    item = BidNote(workspace_id=workspace.id, scope=note.scope, target_id=note.target_id, body=note.body)
    db.add(item)
    db.commit()
    return {"id": item.id, "body": item.body, "scope": item.scope}


@router.get("/company-profile", response_model=CompanyProfileRead)
def get_company_profile(user: User = Depends(current_user), db: Session = Depends(get_db)) -> CompanyProfile:
    return get_or_create_profile(db, workspace_for_user(db, user).id)


@router.put("/company-profile", response_model=CompanyProfileRead)
def save_company_profile(update: CompanyProfileUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> CompanyProfile:
    return update_profile(db, update, workspace_for_user(db, user).id)


@router.get("/company-evidence", response_model=list[EvidenceDocumentRead])
def list_company_evidence(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list:
    from .models import CompanyEvidenceDocument
    workspace_id = workspace_for_user(db, user).id
    return list(db.scalars(select(CompanyEvidenceDocument).where(CompanyEvidenceDocument.workspace_id == workspace_id).order_by(CompanyEvidenceDocument.created_at.desc())))


@router.post("/company-evidence", response_model=EvidenceDocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_company_evidence(file: UploadFile = File(...), category: str = Form("OTHER"), description: str | None = Form(None), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF evidence documents are supported.")
    content = await file.read()
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Evidence documents exceed the upload size limit.")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="The uploaded evidence is not a valid PDF.")
    try:
        workspace_id = workspace_for_user(db, user).id
        document = store_pdf(db, file.filename, content, workspace_id, category, description)
        _record_usage(db, user, "upload_evidence", "DOCUMENT_PROCESSING", workspace_id)
        return document
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=400, detail="The evidence PDF could not be extracted.") from error


@router.get("/company-evidence/search", response_model=list[EvidenceSearchResult])
def search_company_evidence(q: str = Query(min_length=3, max_length=300), user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    return search_evidence(db, q, workspace_for_user(db, user).id)


@router.patch("/company-evidence/{document_id}", response_model=EvidenceDocumentRead)
def update_company_evidence(document_id: int, update: EvidenceMetadataUpdate, db: Session = Depends(get_db)):
    from .models import CompanyEvidenceDocument
    document = db.get(CompanyEvidenceDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Evidence document not found.")
    if update.category is not None:
        if update.category not in CATEGORIES:
            raise HTTPException(status_code=400, detail="Invalid evidence category.")
        document.category = update.category
    if update.description is not None:
        document.description = update.description
    if update.expiry_date is not None:
        document.expiry_date = update.expiry_date
    db.commit()
    db.refresh(document)
    return document


@router.delete("/company-evidence/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company_evidence(document_id: int, db: Session = Depends(get_db)) -> None:
    from .models import CompanyEvidenceChunk, CompanyEvidenceDocument, TenderRequirementEvidence
    document = db.get(CompanyEvidenceDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Evidence document not found.")
    db.execute(delete(CompanyEvidenceChunk).where(CompanyEvidenceChunk.document_id == document_id))
    db.execute(delete(TenderRequirementEvidence).where(TenderRequirementEvidence.document_id == document_id))
    db.delete(document)
    db.commit()
    evidence_storage.delete(document.stored_filename)




def _require_pdf(file: UploadFile) -> None:
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    if file.content_type not in ("application/pdf", "application/octet-stream", None):
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid PDF.")


@router.get("/tenders", response_model=list[TenderListItem])
def list_tenders(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Tender]:
    return list(db.scalars(select(Tender).where(Tender.workspace_id.in_(select(Workspace.id).where(Workspace.user_id == user.id))).order_by(Tender.created_at.desc())))


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    tenders = list(db.scalars(select(Tender).where(Tender.workspace_id.in_(select(Workspace.id).where(Workspace.user_id == user.id))).order_by(Tender.deadline.asc().nulls_last(), Tender.created_at.desc())))
    rows = []
    for tender in tenders:
        mandatory = len(list(db.scalars(select(TenderRequirement.id).where(TenderRequirement.tender_id == tender.id, TenderRequirement.mandatory.is_(True)))))
        unresolved = len(list(db.scalars(select(TenderQuestion.id).where(TenderQuestion.tender_id == tender.id))))
        checklist = list(db.scalars(select(TenderChecklistItem).where(TenderChecklistItem.tender_id == tender.id)))
        assessment = "INSUFFICIENT_INFORMATION"
        if tender.analysis_json:
            try:
                assessment = json.loads(tender.analysis_json).get("bid_assessment", {}).get("recommendation", assessment)
            except json.JSONDecodeError:
                pass
        rows.append({"id": tender.id, "title": tender.title, "reference": tender.reference, "organization": tender.organization, "deadline": tender.deadline, "analysis_status": tender.analysis_status, "risk": tender.risk, "status": tender.status, "assessment": assessment, "mandatory_requirements": mandatory, "unresolved_clarifications": unresolved, "checklist_total": len(checklist), "checklist_done": sum(item.status == "DONE" for item in checklist), "last_analyzed": tender.analysis_completed_at})
    return {"tenders": rows, "totals": {"active": sum(t.status not in {TenderStatus.CLOSED, TenderStatus.NO_BID} for t in tenders), "needs_review": sum(t.analysis_status != AnalysisStatus.COMPLETED or t.status == TenderStatus.REVIEWING for t in tenders), "open_checklist": sum(item.status != "DONE" for t in tenders for item in t.checklist_items)}}


@router.get("/tenders/{tender_id}", response_model=TenderDetail)
def get_tender(tender_id: int, db: Session = Depends(get_db)) -> Tender:
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found.")
    return tender


@router.get("/tenders/{tender_id}/requirements", response_model=list[RequirementRead])
def list_requirements(tender_id: int, db: Session = Depends(get_db)) -> list[TenderRequirement]:
    if not db.get(Tender, tender_id):
        raise HTTPException(status_code=404, detail="Tender not found.")
    return list(db.scalars(select(TenderRequirement).where(TenderRequirement.tender_id == tender_id)))




@router.get("/tenders/{tender_id}/insight", response_model=TenderInsight)
def get_insight(tender_id: int, db: Session = Depends(get_db)) -> dict:
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found.")
    def row_dict(row: object) -> dict:
        return {key: value for key, value in vars(row).items() if not key.startswith("_")}

    return {
        "tender": tender,
        "analysis": json.loads(tender.analysis_json) if tender.analysis_json else None,
        "requirements": [row_dict(item) for item in db.scalars(select(TenderRequirement).where(TenderRequirement.tender_id == tender_id))],
        "documents": [row_dict(item) for item in db.scalars(select(TenderDocument).where(TenderDocument.tender_id == tender_id))],
        "dates": [row_dict(item) for item in db.scalars(select(TenderDate).where(TenderDate.tender_id == tender_id))],
        "risks": [row_dict(item) for item in db.scalars(select(TenderRisk).where(TenderRisk.tender_id == tender_id))],
        "clarifications": [row_dict(item) for item in db.scalars(select(TenderQuestion).where(TenderQuestion.tender_id == tender_id))],
        "sources": [row_dict(item) for item in db.scalars(select(TenderSource).where(TenderSource.tender_id == tender_id))],
        "checklist": [row_dict(item) for item in db.scalars(select(TenderChecklistItem).where(TenderChecklistItem.tender_id == tender_id).order_by(TenderChecklistItem.id))],
    }


@router.get("/tenders/{tender_id}/checklist", response_model=list[ChecklistItemRead])
def get_checklist(tender_id: int, db: Session = Depends(get_db)) -> list[TenderChecklistItem]:
    if not db.get(Tender, tender_id):
        raise HTTPException(status_code=404, detail="Tender not found.")
    return list(db.scalars(select(TenderChecklistItem).where(TenderChecklistItem.tender_id == tender_id).order_by(TenderChecklistItem.id)))


@router.get("/tenders/{tender_id}/dates")
def get_dates(tender_id: int, db: Session = Depends(get_db)) -> list[dict]:
    if not db.get(Tender, tender_id):
        raise HTTPException(status_code=404, detail="Tender not found.")
    return [{key: value for key, value in vars(item).items() if not key.startswith("_")} for item in db.scalars(select(TenderDate).where(TenderDate.tender_id == tender_id))]


@router.get("/tenders/{tender_id}/risks")
def get_risks(tender_id: int, db: Session = Depends(get_db)) -> list[dict]:
    if not db.get(Tender, tender_id):
        raise HTTPException(status_code=404, detail="Tender not found.")
    return [{key: value for key, value in vars(item).items() if not key.startswith("_")} for item in db.scalars(select(TenderRisk).where(TenderRisk.tender_id == tender_id))]


@router.get("/tenders/{tender_id}/clarifications")
def get_clarifications(tender_id: int, db: Session = Depends(get_db)) -> list[dict]:
    if not db.get(Tender, tender_id):
        raise HTTPException(status_code=404, detail="Tender not found.")
    return [{key: value for key, value in vars(item).items() if not key.startswith("_")} for item in db.scalars(select(TenderQuestion).where(TenderQuestion.tender_id == tender_id))]


@router.get("/tenders/{tender_id}/assessment")
def get_assessment(tender_id: int, db: Session = Depends(get_db)) -> dict:
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found.")
    if not tender.analysis_json:
        return {"recommendation": "INSUFFICIENT_INFORMATION", "confidence": "LOW", "reasons": [], "verify": []}
    return json.loads(tender.analysis_json).get("bid_assessment", {})


@router.get("/tenders/{tender_id}/company-assessment", response_model=CompanyAssessmentRead)
def get_company_assessment(tender_id: int, db: Session = Depends(get_db)) -> dict:
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found.")
    assessment = db.scalar(select(TenderAssessment).where(TenderAssessment.tender_id == tender_id).order_by(TenderAssessment.created_at.desc()))
    if not assessment:
        raise HTTPException(status_code=404, detail="Company assessment not available yet.")
    return _assessment_response(db, assessment)


@router.post("/tenders/{tender_id}/company-assessment", response_model=CompanyAssessmentRead)
def create_company_assessment(tender_id: int, db: Session = Depends(get_db)) -> dict:
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found.")
    if tender.analysis_status != AnalysisStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Tender analysis must be complete before company matching.")
    return _assessment_response(db, assess_tender(db, tender))


@router.patch("/company-actions/{action_id}", response_model=dict)
def update_company_action(action_id: int, update: BidActionStatusUpdate, db: Session = Depends(get_db)) -> dict:
    action = db.get(TenderBidAction, action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Bid action not found.")
    action.status = update.status
    db.commit()
    return {"id": action.id, "status": action.status}


@router.patch("/checklist/{item_id}", response_model=ChecklistItemRead)
def update_checklist(item_id: int, update: ChecklistStatusUpdate, db: Session = Depends(get_db)) -> TenderChecklistItem:
    if update.status not in CHECKLIST_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid checklist status.")
    item = db.get(TenderChecklistItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found.")
    item.status = update.status
    db.commit()
    db.refresh(item)
    return item


@router.post("/tenders/{tender_id}/ask", response_model=AskResponse)
def ask_tender(tender_id: int, question: str = Query(min_length=3, max_length=500), user: User = Depends(current_user), db: Session = Depends(get_db)) -> AskResponse:
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found.")
    if tender.analysis_status != AnalysisStatus.COMPLETED:
        return AskResponse(answer="I couldn't search this tender because its analysis is not complete yet.", sources=[])
    limit_error = check_ai_allowance(db, user)
    if limit_error:
        return JSONResponse(status_code=403, content=limit_error)
    sources = list(db.scalars(select(TenderSource).where(TenderSource.tender_id == tender_id)))
    answer, matched = answer_question(question, sources)
    _record_usage(db, user, "ask_question", "AI_QUESTION", tender.workspace_id, tender.id)
    return AskResponse(answer=answer, sources=[SourceRead.model_validate(source) for source in matched])


@router.post("/tenders", response_model=TenderDetail, status_code=status.HTTP_201_CREATED)
async def create_tender(background_tasks: BackgroundTasks, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)) -> Tender:
    _require_pdf(file)
    content = await file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"PDFs must be smaller than {settings.max_upload_size_mb} MB.")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="The uploaded file does not contain a valid PDF header.")
    limit_error = check_tender_allowance(db, user)
    if limit_error:
        return JSONResponse(status_code=403, content=limit_error)

    safe_name = f"{uuid4().hex}.pdf"
    document_storage.save(safe_name, content)
    original_name = Path(file.filename or "tender.pdf").name
    tender = Tender(
        workspace_id=workspace_for_user(db, user).id,
        title=Path(original_name).stem.replace("_", " ").replace("-", " "),
        file_name=original_name,
        stored_file_name=safe_name,
        status=TenderStatus.NEW,
        analysis_status=AnalysisStatus.QUEUED,
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)
    _record_usage(db, user, "upload_tender", "TENDER_UPLOAD", tender.workspace_id, tender.id)
    enqueue_analysis(tender.id, background_tasks)
    return tender


@router.post("/tenders/{tender_id}/retry", response_model=TenderDetail)
def retry_analysis(tender_id: int, background_tasks: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Tender:
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found.")
    if tender.analysis_status in {AnalysisStatus.QUEUED, AnalysisStatus.PROCESSING}:
        raise HTTPException(status_code=409, detail="Tender analysis is already in progress.")
    if not db.scalar(select(UsageEvent.id).where(UsageEvent.tender_id == tender_id, UsageEvent.usage_type == "TENDER_UPLOAD")):
        limit_error = check_tender_allowance(db, user)
        if limit_error:
            return JSONResponse(status_code=403, content=limit_error)
        _record_usage(db, user, "upload_tender_retry", "TENDER_UPLOAD", tender.workspace_id, tender.id)
    tender.analysis_status = AnalysisStatus.QUEUED
    tender.analysis_error = None
    tender.progress = 0
    db.commit()
    enqueue_analysis(tender.id, background_tasks)
    return tender


@router.post("/tenders/{tender_id}/reanalyze", response_model=TenderDetail)
def reanalyze_tender(tender_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> Tender:
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found.")
    if tender.analysis_status in {AnalysisStatus.QUEUED, AnalysisStatus.PROCESSING}:
        raise HTTPException(status_code=409, detail="Tender analysis is already in progress.")
    tender.analysis_status = AnalysisStatus.QUEUED
    tender.analysis_error = None
    tender.progress = 0
    db.commit()
    enqueue_analysis(tender.id, background_tasks)
    return tender


@router.get("/overview")
def overview(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Action-oriented product overview for the dashboard and tender library.

    Everything is derived from persisted data. Nothing is invented: a tender
    only appears under an action when the underlying deterministic statuses
    say so, and missing company information never becomes NO_BID.
    """
    tenders = list(db.scalars(select(Tender).where(Tender.workspace_id.in_(select(Workspace.id).where(Workspace.user_id == user.id))).order_by(Tender.created_at.desc())))
    workspaces = {workspace.tender_id: workspace for workspace in db.scalars(select(BidWorkspace))}
    packages = {package.workspace_id: package for package in db.scalars(select(BidPackage))}
    recommendations: dict[int, str] = {}
    for assessment in db.scalars(select(TenderAssessment).order_by(TenderAssessment.created_at.desc())):
        recommendations.setdefault(assessment.tender_id, assessment.recommendation)
    actions = {"needs_attention": [], "awaiting_decision": [], "preparing": [], "missing_documents": [], "blocked": [], "ready_for_review": [], "submitted": [], "no_bid": []}
    rows, deadlines, recent = [], [], []
    for tender in tenders:
        # Materialise the workspace exactly like the detail endpoints do, so the
        # dashboard reflects real statuses immediately after analysis.
        workspace = workspaces.get(tender.id) or get_or_create_workspace(db, tender)
        decision = workspace.decision
        workflow = workspace.workflow_status
        readiness = readiness_summary(db, workspace)
        blockers = readiness.get("blockers", [])
        updated = workspace.updated_at
        if tender.analysis_completed_at and (updated is None or tender.analysis_completed_at > updated):
            updated = tender.analysis_completed_at
        rows.append({"id": tender.id, "title": tender.title, "reference": tender.reference, "organization": tender.organization, "closing": tender.deadline.isoformat() if tender.deadline else None, "analysis_status": tender.analysis_status, "risk": tender.risk, "tender_status": tender.status, "workflow_status": workflow, "decision": decision, "recommendation": recommendations.get(tender.id, "INSUFFICIENT_INFORMATION"), "readiness_state": readiness["state"], "blocker_count": len(blockers), "blockers": blockers, "updated_at": updated.isoformat() if updated else None})
        if tender.analysis_status != AnalysisStatus.COMPLETED:
            actions["needs_attention"].append(tender.id)
        if decision == "PENDING" and workflow not in {"NO_BID", "SUBMITTED"}:
            actions["awaiting_decision"].append(tender.id)
        if workflow in {"UNDER_REVIEW", "PREPARING"}:
            actions["preparing"].append(tender.id)
        if blockers:
            actions["blocked"].append(tender.id)
            if any(blocker["kind"] in {"document", "workspace"} for blocker in blockers):
                actions["missing_documents"].append(tender.id)
        if workflow in {"READY_FOR_REVIEW", "READY_TO_SUBMIT"}:
            actions["ready_for_review"].append(tender.id)
        if workflow == "SUBMITTED":
            actions["submitted"].append(tender.id)
        if workflow == "NO_BID" or decision == "NO_BID":
            actions["no_bid"].append(tender.id)
        if tender.deadline:
            deadlines.append({"tender_id": tender.id, "title": tender.title, "reference": tender.reference, "closing": tender.deadline.isoformat(), "days_remaining": (tender.deadline.date() - datetime.now().date()).days})
        if updated:
            recent.append({"tender_id": tender.id, "title": tender.title, "reference": tender.reference, "updated_at": updated.isoformat(), "readiness_state": readiness["state"]})
    deadlines = sorted(deadlines, key=lambda row: row["days_remaining"])[:8]
    recent = sorted(recent, key=lambda row: row["updated_at"], reverse=True)[:5]
    return {"actions": {name: {"count": len(ids), "tender_ids": ids} for name, ids in actions.items()}, "deadlines": deadlines, "recent": recent, "tenders": rows, "totals": {"tenders": len(tenders)}}


@router.get("/usage")
def usage_summary(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    usage = get_current_usage(db, user)
    events = list(db.scalars(select(UsageEvent).where(UsageEvent.user_id == user.id).order_by(UsageEvent.created_at.desc())))
    totals: dict[str, int] = {}
    for event in events:
        totals[event.usage_type] = totals.get(event.usage_type, 0) + event.quantity
    return {**usage, "totals": totals, "events": [{"action": event.action, "usage_type": event.usage_type, "quantity": event.quantity, "created_at": event.created_at} for event in events]}


@router.get("/plan")
def plan_summary(user: User = Depends(current_user)) -> dict:
    plan = get_plan_definition(user.plan)
    return {"plan": plan.key, "display_name": plan.display_name, "monthly_price_sgd": plan.monthly_price_sgd, "entitlements": plan.to_dict()}


@router.get("/plans")
def plan_catalog(user: User = Depends(current_user)) -> dict:
    return {"plans": [{**PLAN_DEFINITIONS[key].to_dict(), "payment_available": False} for key in PLAN_DISPLAY_ORDER]}


@router.get("/tenders/{tender_id}/bid-documents/{bid_document_id}/evidence-suggestions")
def bid_document_evidence_suggestions(tender_id: int, bid_document_id: int, db: Session = Depends(get_db)) -> dict:
    """Conservative lexical evidence suggestions for one required bid document.

    Suggestions are POTENTIALLY_RELEVANT candidates only. Nothing is linked
    automatically and no suggestion claims the requirement is satisfied; the
    explicit user selection through the link endpoint creates the actual link.
    """
    bid_document = db.get(BidDocument, bid_document_id)
    workspace = db.get(BidWorkspace, bid_document.workspace_id) if bid_document else None
    if not bid_document or not workspace or workspace.tender_id != tender_id:
        raise HTTPException(status_code=404, detail="Bid document not found.")
    requested = db.get(TenderDocument, bid_document.tender_document_id)
    query_parts = [requested.name]
    requirement = db.scalar(select(TenderRequirement).where(TenderRequirement.tender_id == tender_id, TenderRequirement.requirement == requested.name))
    if requirement:
        query_parts.append(requirement.requirement)
    if requested.source_snippet:
        query_parts.append(requested.source_snippet[:300])
    tender_workspace_id = db.scalar(select(Tender.workspace_id).where(Tender.id == tender_id))
    linked_ids = {link.evidence_document_id for link in db.scalars(select(BidDocumentLink).where(BidDocumentLink.bid_document_id == bid_document_id))}
    chunks = [{"chunk_id": chunk.chunk_id, "page": chunk.page, "text": chunk.text, "document_id": chunk.document_id} for chunk in db.scalars(select(CompanyEvidenceChunk).join(CompanyEvidenceDocument, CompanyEvidenceChunk.document_id == CompanyEvidenceDocument.id).where(CompanyEvidenceDocument.workspace_id == tender_workspace_id))]
    document_by_chunk = {(chunk["chunk_id"], chunk["page"]): chunk["document_id"] for chunk in chunks}
    documents = {document.id: document for document in db.scalars(select(CompanyEvidenceDocument).where(CompanyEvidenceDocument.workspace_id == tender_workspace_id))}
    suggestions: list[dict] = []
    seen: set[int] = set()
    for match in retrieve(chunks, " ".join(query_parts), limit=24):
        document = documents.get(document_by_chunk.get((match.chunk_id, match.page)))
        if not document or document.id in linked_ids or document.id in seen:
            continue
        seen.add(document.id)
        suggestions.append({"document_id": document.id, "filename": document.filename, "category": document.category, "description": document.description, "page_count": document.page_count, "expiry_date": document.expiry_date, "page": match.page, "snippet": match.text[:280], "score": round(match.score, 3), "relevance": "POTENTIALLY_RELEVANT", "review_required": True})
        if len(suggestions) >= 6:
            break
    return {"bid_document_id": bid_document_id, "requested_document": requested.name, "suggestions": suggestions}


@router.delete("/bid-documents/{bid_document_id}/evidence/{evidence_document_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_bid_document_evidence(bid_document_id: int, evidence_document_id: int, db: Session = Depends(get_db)) -> None:
    link = db.scalar(select(BidDocumentLink).where(BidDocumentLink.bid_document_id == bid_document_id, BidDocumentLink.evidence_document_id == evidence_document_id))
    if not link:
        raise HTTPException(status_code=404, detail="Evidence link not found.")
    db.delete(link)
    db.commit()


@router.get("/company-evidence/{document_id}/usage")
def company_evidence_usage(document_id: int, db: Session = Depends(get_db)) -> dict:
    """Where a company evidence document is used across tenders and packages."""
    document = db.get(CompanyEvidenceDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Evidence document not found.")
    requirements = []
    for link in db.scalars(select(TenderRequirementEvidence).where(TenderRequirementEvidence.document_id == document_id)):
        requirement = db.get(TenderRequirement, link.requirement_id)
        tender = db.get(Tender, requirement.tender_id) if requirement else None
        if requirement and tender:
            requirements.append({"tender_id": tender.id, "tender_title": tender.title, "requirement": requirement.requirement, "page": link.page, "relevance": link.relevance})
    bids = []
    for link in db.scalars(select(BidDocumentLink).where(BidDocumentLink.evidence_document_id == document_id)):
        bid_document = db.get(BidDocument, link.bid_document_id)
        if not bid_document:
            continue
        workspace = db.get(BidWorkspace, bid_document.workspace_id)
        tender = db.get(Tender, workspace.tender_id)
        requested = db.get(TenderDocument, bid_document.tender_document_id)
        bids.append({"tender_id": tender.id, "tender_title": tender.title, "requested_document": requested.name if requested else None})
    packages = []
    for item in db.scalars(select(BidPackageItem).where(BidPackageItem.evidence_document_id == document_id)):
        package = db.get(BidPackage, item.package_id)
        workspace = db.get(BidWorkspace, package.workspace_id)
        tender = db.get(Tender, workspace.tender_id)
        packages.append({"tender_id": tender.id, "tender_title": tender.title, "included": item.included, "status": item.status})
    return {"document_id": document_id, "filename": document.filename, "requirements": requirements, "bids": bids, "packages": packages}


