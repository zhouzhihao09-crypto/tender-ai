import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..evidence_schemas import EvidenceMetadataUpdate
from ..models import CompanyEvidenceChunk, CompanyEvidenceDocument, TenderRequirementEvidence
from ..storage import evidence_storage
from .pdf_service import chunk_pages, extract_pdf_pages
from .retrieval_service import retrieve


CATEGORIES = {"REGISTRATION", "LICENCE", "CERTIFICATION", "INSURANCE", "PROJECT_REFERENCE", "COMPANY_PROFILE", "SAFETY", "TECHNICAL", "OTHER"}


def explicit_expiry(text: str) -> str | None:
    match = re.search(r"(?:expiry(?:\s+date)?|expires?|valid until|validity until)\s*[:\-]?\s*(\d{1,2}[ ./-](?:\d{1,2}|[A-Za-z]{3,9})[ ./-]\d{2,4}|\d{4}[ ./-]\d{1,2}[ ./-]\d{1,2})", text, re.IGNORECASE)
    return match.group(1) if match else None


def store_pdf(db: Session, filename: str, content: bytes, workspace_id: int, category: str = "OTHER", description: str | None = None) -> CompanyEvidenceDocument:
    if category not in CATEGORIES:
        raise ValueError("Invalid evidence category")
    stored = f"{uuid4().hex}.pdf"
    evidence_storage.save(stored, content)
    with evidence_storage.materialize(stored) as path:
        try:
            pages = extract_pdf_pages(path)
            chunks = chunk_pages(pages)
        except Exception:
            evidence_storage.delete(stored)
            raise
        text = "\n".join(page.text for page in pages)
        document = CompanyEvidenceDocument(workspace_id=workspace_id, filename=Path(filename).name, stored_filename=stored, category=category, description=description, page_count=len(pages), expiry_date=explicit_expiry(text), extracted_text=text)
        db.add(document)
        db.flush()
        for chunk in chunks:
            db.add(CompanyEvidenceChunk(document_id=document.id, page=int(chunk["page"]), chunk_id=str(chunk["chunk_id"]), text=str(chunk["text"])))
        db.commit()
        db.refresh(document)
    return document


def search_evidence(db: Session, query: str, workspace_id: int, limit: int = 10) -> list[dict]:
    chunks = list(db.scalars(select(CompanyEvidenceChunk).join(CompanyEvidenceDocument, CompanyEvidenceChunk.document_id == CompanyEvidenceDocument.id).where(CompanyEvidenceDocument.workspace_id == workspace_id)))
    results = retrieve([{"chunk_id": item.chunk_id, "page": item.page, "text": item.text, "document_id": item.document_id} for item in chunks], query, limit=limit)
    by_key = {(item.chunk_id, item.page): item for item in chunks}
    docs = {doc.id: doc for doc in db.scalars(select(CompanyEvidenceDocument))}
    seen: set[tuple[int, int]] = set()
    output = []
    for result in results:
        chunk = by_key[(result.chunk_id, result.page)]
        key = (chunk.document_id, result.page)
        if key in seen:
            continue
        seen.add(key)
        output.append({"document_id": chunk.document_id, "filename": docs[chunk.document_id].filename, "category": docs[chunk.document_id].category, "page": result.page, "chunk_id": result.chunk_id, "snippet": result.text, "score": result.score})
    return output


def link_requirement_evidence(db: Session, requirement_id: int, requirement_text: str, workspace_id: int) -> list[dict]:
    query = requirement_text
    if re.search(r"relevant experience|similar project", query, re.IGNORECASE):
        query += " similar project"
    if re.search(r"BOQ|bill of quantities", query, re.IGNORECASE):
        query += " completed BOQ"
    matches = search_evidence(db, query, workspace_id, limit=5)
    for match in matches:
        db.add(TenderRequirementEvidence(requirement_id=requirement_id, document_id=match["document_id"], chunk_id=match["chunk_id"], page=match["page"], snippet=match["snippet"], relevance="INFERENCE"))
    db.commit()
    return matches