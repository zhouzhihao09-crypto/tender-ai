import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import BidDocument, BidDocumentLink, BidPackage, BidPackageExport, BidPackageItem, BidWorkspace, CompanyEvidenceDocument, Tender, TenderDate
from ..storage import evidence_storage, export_storage
from .bid_service import workspace_payload


def _safe_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip(" .")
    return clean or "document.pdf"


def get_or_create_package(db: Session, workspace: BidWorkspace) -> BidPackage:
    package = db.scalar(select(BidPackage).where(BidPackage.workspace_id == workspace.id))
    if package:
        return package
    package = BidPackage(workspace_id=workspace.id)
    db.add(package)
    db.flush()
    for order, document in enumerate(db.scalars(select(BidDocument).where(BidDocument.workspace_id == workspace.id)), start=1):
        db.add(BidPackageItem(package_id=package.id, bid_document_id=document.id, display_order=order, status=document.status))
    db.commit()
    db.refresh(package)
    return package


def package_payload(db: Session, package: BidPackage) -> dict:
    workspace = db.get(BidWorkspace, package.workspace_id)
    workspace_data = workspace_payload(db, workspace)
    documents = {item["id"]: item for item in workspace_data["documents"]}
    items = []
    for item in db.scalars(select(BidPackageItem).where(BidPackageItem.package_id == package.id).order_by(BidPackageItem.display_order, BidPackageItem.id)):
        bid_document = documents.get(item.bid_document_id) if item.bid_document_id else None
        evidence = db.get(CompanyEvidenceDocument, item.evidence_document_id) if item.evidence_document_id else None
        items.append({"id": item.id, "name": evidence.filename if evidence else (bid_document["requested_document"] if bid_document else "Document"), "category": evidence.category if evidence else "SUBMISSION", "source": "COMPANY_EVIDENCE" if evidence else "TENDER_REQUESTED", "bid_document_id": item.bid_document_id, "evidence_document_id": item.evidence_document_id, "related_requirement": bid_document["requested_document"] if bid_document else None, "source_page": bid_document["source_page"] if bid_document else None, "included": item.included, "status": item.status, "notes": item.notes, "display_order": item.display_order})
    blocking = []
    blockers = []
    if workspace_data["readiness"]["state"] in {"NOT_READY", "INSUFFICIENT_INFORMATION"}:
        blocking.append(workspace_data["readiness"]["reason"])
        blockers.append({"kind": "workspace", "label": workspace_data["readiness"]["reason"], "action": "Resolve the bid preparation blockers for this tender.", "target_id": None})
    for item in items:
        if item["included"] and item["status"] in {"MISSING", "REVIEW_REQUIRED"}:
            reason = f"{item['name']} is {item['status']}."
            blocking.append(reason)
            blockers.append({"kind": "package_item", "label": reason, "action": "Update the document status or link the final evidence.", "target_id": item["id"]})
    if not items:
        readiness = "INSUFFICIENT_INFORMATION"
    elif blocking:
        readiness = "NOT_READY" if any("MISSING" in text or "not ready" in text.lower() for text in blocking) else "REVIEW_REQUIRED"
    elif all(item["status"] == "READY" for item in items if item["included"]):
        readiness = "READY"
    else:
        readiness = "ALMOST_READY"
    ready_reason = "Based on the tracked package items, all included documents are marked ready. This is not a compliance determination."
    return {"id": package.id, "workspace_id": package.workspace_id, "status": package.status, "review_status": package.review_status, "items": items, "workspace_readiness": workspace_data["readiness"], "blockers": blockers, "readiness": {"state": readiness, "reasons": blocking or [ready_reason if readiness == "READY" else "Included package items need final review before export."]}, "exports": [{"id": record.id, "filename": record.filename, "readiness_state": record.readiness_state, "included_count": record.included_count, "incomplete": record.incomplete, "created_at": record.created_at} for record in db.scalars(select(BidPackageExport).where(BidPackageExport.package_id == package.id).order_by(BidPackageExport.created_at.desc()))]}


def export_package(db: Session, package: BidPackage, allow_incomplete: bool) -> tuple[Path, dict]:
    payload = package_payload(db, package)
    if payload["readiness"]["state"] not in {"READY", "ALMOST_READY"} and not allow_incomplete:
        raise ValueError("Export blocked: " + " ".join(payload["readiness"]["reasons"]))
    workspace = db.get(BidWorkspace, package.workspace_id)
    tender = db.get(Tender, workspace.tender_id)
    filename = f"{_safe_name(tender.title)}_submission_package_{datetime.utcnow():%Y%m%d_%H%M%S}.zip"
    output = export_storage.writable_path(filename)
    closing = next((date for date in db.scalars(select(TenderDate).where(TenderDate.tender_id == tender.id)) if "closing" in date.event.lower() or "submission" in date.event.lower()), None)
    closing_value = f"{closing.date_value} {closing.time_value or ''}".strip() if closing else (tender.deadline.isoformat() if tender.deadline else None)
    manifest = {"tender": tender.title, "reference": tender.reference, "closing": closing_value, "generated_at": datetime.utcnow().isoformat(), "package_readiness": payload["readiness"], "documents": payload["items"]}
    try:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("submission_manifest.json", json.dumps(manifest, indent=2))
            archive.writestr("README.txt", "Tender submission package. Review the manifest and package readiness before submitting.\n")
            used_names: set[str] = set()
            for item in payload["items"]:
                if not item["included"] or not item["evidence_document_id"]:
                    continue
                document = db.get(CompanyEvidenceDocument, item["evidence_document_id"])
                if evidence_storage.exists(document.stored_filename):
                    name = _safe_name(document.filename)
                    while name in used_names:
                        name = f"{len(used_names)}_{name}"
                    used_names.add(name)
                    archive.writestr(f"documents/{name}", evidence_storage.read(document.stored_filename))
        export_storage.save(filename, output.read_bytes())
        db.add(BidPackageExport(package_id=package.id, filename=filename, readiness_state=payload["readiness"]["state"], included_count=sum(item["included"] for item in payload["items"]), incomplete=payload["readiness"]["state"] not in {"READY", "ALMOST_READY"}))
        package.status = "EXPORTED"
        db.commit()
    except Exception:
        # The build artifact is only safe to remove here because the caller
        # streams the returned path; on failure nothing is being streamed.
        export_storage.release_path(output)
        raise
    return output, payload