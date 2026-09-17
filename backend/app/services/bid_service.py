from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BidClarification, BidDocument, BidDocumentLink, BidNote, BidRequirement, BidWorkspace, Tender, TenderDocument, TenderQuestion, TenderRequirement


def get_or_create_workspace(db: Session, tender: Tender) -> BidWorkspace:
    workspace = db.scalar(select(BidWorkspace).where(BidWorkspace.tender_id == tender.id))
    if workspace:
        return workspace
    workspace = BidWorkspace(tender_id=tender.id)
    db.add(workspace)
    db.flush()
    for requirement in db.scalars(select(TenderRequirement).where(TenderRequirement.tender_id == tender.id)):
        db.add(BidRequirement(workspace_id=workspace.id, requirement_id=requirement.id, status="NOT_STARTED"))
    for document in db.scalars(select(TenderDocument).where(TenderDocument.tender_id == tender.id)):
        db.add(BidDocument(workspace_id=workspace.id, tender_document_id=document.id, status="MISSING"))
    for question in db.scalars(select(TenderQuestion).where(TenderQuestion.tender_id == tender.id)):
        db.add(BidClarification(workspace_id=workspace.id, question_id=question.id, question=question.question))
    db.commit()
    db.refresh(workspace)
    return workspace


def requirement_rows(db: Session, workspace: BidWorkspace) -> list[dict]:
    rows = []
    for item in db.scalars(select(BidRequirement).where(BidRequirement.workspace_id == workspace.id)):
        requirement = db.get(TenderRequirement, item.requirement_id)
        rows.append({"id": item.id, "requirement": requirement.requirement, "mandatory": requirement.mandatory, "status": item.status, "notes": item.notes, "source_page": requirement.source_page, "source_snippet": requirement.source_snippet})
    return rows


def document_rows(db: Session, workspace: BidWorkspace) -> list[dict]:
    rows = []
    for item in db.scalars(select(BidDocument).where(BidDocument.workspace_id == workspace.id)):
        requested = db.get(TenderDocument, item.tender_document_id)
        links = list(db.scalars(select(BidDocumentLink).where(BidDocumentLink.bid_document_id == item.id)))
        rows.append({"id": item.id, "requested_document": requested.name, "status": item.status, "notes": item.notes, "source_page": requested.source_page, "source_snippet": requested.source_snippet, "evidence_document_ids": [link.evidence_document_id for link in links]})
    return rows


def clarification_rows(db: Session, workspace: BidWorkspace) -> list[dict]:
    rows = []
    for item in db.scalars(select(BidClarification).where(BidClarification.workspace_id == workspace.id)):
        question = db.get(TenderQuestion, item.question_id)
        rows.append({"id": item.id, "question": item.question, "status": item.status, "answer": item.answer, "source_page": question.source_page if question else None})
    return rows


def readiness_parts(requirements: list[dict], documents: list[dict], clarifications: list[dict], decision: str) -> tuple[dict, list[dict], list[dict], list[dict], int, int, int, int]:
    """Deterministic readiness shared by the workspace payload and the overview.

    `blocking` contains only causes that the deterministic readiness logic
    treats as NOT_READY or REVIEW_REQUIRED. Anything that merely keeps the
    workspace from being fully READY is listed as attention. Missing company
    information is never treated as a blocker.
    """
    missing = [{"title": item["requirement"], "why": "A mandatory tender requirement is not ready.", "source_page": item["source_page"], "action": "Review or prepare supporting submission material."} for item in requirements if item["mandatory"] and item["status"] in {"MISSING", "BLOCKED", "NOT_STARTED"}]
    missing += [{"title": item["requested_document"], "why": "The tender asks for this document, but the bid package is not ready.", "source_page": item["source_page"], "action": "Link existing evidence or prepare the requested submission document."} for item in documents if item["status"] == "MISSING"]
    ready_requirements = sum(item["status"] in {"READY", "NOT_APPLICABLE"} for item in requirements)
    ready_documents = sum(item["status"] == "READY" for item in documents)
    blocked = sum(item["status"] == "BLOCKED" for item in requirements)
    unresolved = sum(item["status"] not in {"ANSWERED", "CLOSED"} for item in clarifications)
    if not requirements and not documents:
        readiness = {"state": "INSUFFICIENT_INFORMATION", "reason": "No tender submission requirements have been extracted."}
    elif blocked or missing:
        readiness = {"state": "NOT_READY", "reason": "Mandatory requirements, required documents, or blockers still need attention."}
    elif unresolved:
        readiness = {"state": "REVIEW_REQUIRED", "reason": "Clarification questions remain unresolved."}
    elif ready_requirements == len(requirements) and ready_documents == len(documents):
        readiness = {"state": "READY", "reason": "Based on the information currently available, all tracked items are ready. This is not a legal compliance determination."}
    else:
        readiness = {"state": "ALMOST_READY", "reason": "Tracked items remain in progress or need final review."}
    blocking: list[dict] = []
    attention: list[dict] = []
    if decision == "PENDING":
        attention.append({"kind": "decision", "label": "Bid decision has not been recorded yet.", "action": "Record your BID / NO_BID decision in the Bid decision section.", "section": "decision", "target_id": None})
    for item in requirements:
        if item["mandatory"] and item["status"] in {"MISSING", "BLOCKED", "NOT_STARTED"}:
            blocking.append({"kind": "requirement", "label": f"Mandatory requirement not ready: {item['requirement']}", "action": "Review the requirement and update its status in Bid preparation.", "section": "requirements", "target_id": item["id"]})
        elif item["status"] == "BLOCKED":
            blocking.append({"kind": "requirement", "label": f"Requirement blocked: {item['requirement']}", "action": "Resolve the blocker or mark the requirement not applicable.", "section": "requirements", "target_id": item["id"]})
        elif item["status"] == "IN_PROGRESS":
            attention.append({"kind": "requirement", "label": f"Requirement in progress: {item['requirement']}", "action": "Keep preparing the supporting material for this requirement.", "section": "requirements", "target_id": item["id"]})
    for item in documents:
        if item["status"] == "MISSING":
            blocking.append({"kind": "document", "label": f"Document missing: {item['requested_document']}", "action": "Link company evidence or update the document status.", "section": "documents", "target_id": item["id"]})
        elif item["status"] == "REVIEW_REQUIRED":
            attention.append({"kind": "document", "label": f"Document needs review: {item['requested_document']}", "action": "Review the linked evidence and confirm the document status.", "section": "documents", "target_id": item["id"]})
    for item in clarifications:
        if item["status"] not in {"ANSWERED", "CLOSED"}:
            blocking.append({"kind": "clarification", "label": f"Clarification unresolved: {item['question'][:90]}", "action": "Finalise the question, send it, and record the answer.", "section": "clarifications", "target_id": item["id"]})
    return readiness, blocking, attention, missing, ready_requirements, ready_documents, blocked, unresolved


def workspace_payload(db: Session, workspace: BidWorkspace) -> dict:
    tender = db.get(Tender, workspace.tender_id)
    requirements = requirement_rows(db, workspace)
    documents = document_rows(db, workspace)
    clarifications = clarification_rows(db, workspace)
    readiness, blocking, attention, missing, ready_requirements, ready_documents, blocked, unresolved = readiness_parts(requirements, documents, clarifications, workspace.decision)
    notes = [{"id": item.id, "scope": item.scope, "target_id": item.target_id, "body": item.body, "created_at": item.created_at} for item in db.scalars(select(BidNote).where(BidNote.workspace_id == workspace.id).order_by(BidNote.id))]
    return {"id": workspace.id, "tender_id": workspace.tender_id, "workflow_status": workspace.workflow_status, "decision": workspace.decision, "decision_note": workspace.decision_note, "requirements": requirements, "documents": documents, "clarifications": clarifications, "notes": notes, "timeline": [{"event": item.event, "date": item.date_value, "time": item.time_value, "source_page": item.source_page} for item in sorted(tender.dates, key=lambda date: date.date_value)], "missing_items": missing, "readiness": {**readiness, "requirements_ready": f"{ready_requirements} / {len(requirements)}", "documents_ready": f"{ready_documents} / {len(documents)}", "blocked": blocked, "unresolved_clarifications": unresolved, "blockers": blocking, "attention": attention}}


def readiness_summary(db: Session, workspace: BidWorkspace) -> dict:
    """Lightweight workspace readiness for list views (overview/dashboard)."""
    requirements = requirement_rows(db, workspace)
    documents = document_rows(db, workspace)
    clarifications = clarification_rows(db, workspace)
    readiness, blocking, attention, _missing, ready_requirements, ready_documents, blocked, unresolved = readiness_parts(requirements, documents, clarifications, workspace.decision)
    return {**readiness, "requirements_ready": f"{ready_requirements} / {len(requirements)}", "documents_ready": f"{ready_documents} / {len(documents)}", "blocked": blocked, "unresolved_clarifications": unresolved, "blockers": blocking, "attention": attention}