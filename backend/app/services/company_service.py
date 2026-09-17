import json
import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..company_schemas import CompanyProfileUpdate
from ..models import CompanyEvidenceDocument, CompanyProfile, Tender, TenderAssessment, TenderBidAction, TenderRequirement, TenderRequirementEvidence, TenderRequirementMatch
from .evidence_service import link_requirement_evidence


PROFILE_FIELDS = [field for field in CompanyProfileUpdate.model_fields]


def get_or_create_profile(db: Session, workspace_id: int | None = None) -> CompanyProfile:
    if workspace_id is None:
        profile = db.get(CompanyProfile, 1)
        if profile:
            return profile
        workspace_id = db.scalar(select(Tender.workspace_id).where(Tender.workspace_id.is_not(None)).order_by(Tender.id)) or 1
    profile = db.scalar(select(CompanyProfile).where(CompanyProfile.workspace_id == workspace_id))
    if profile:
        return profile
    profile = CompanyProfile(workspace_id=workspace_id, version=1)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def update_profile(db: Session, update: CompanyProfileUpdate, workspace_id: int) -> CompanyProfile:
    profile = get_or_create_profile(db, workspace_id)
    for field in update.model_fields_set:
        setattr(profile, field, getattr(update, field))
    profile.version += 1
    db.commit()
    db.refresh(profile)
    return profile


def _negative(value: str) -> bool:
    return bool(re.search(r"\b(no|none|not|without|does not|don't)\b", value, re.IGNORECASE))


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]{4,}", value.lower()) if token not in {"with", "from", "that", "have", "been"}}


def _company_evidence(profile: CompanyProfile, requirement: TenderRequirement) -> tuple[str | None, str | None, str]:
    text = f"{requirement.requirement} {requirement.source_snippet or ''}".lower()
    if "insurance" in text:
        field = "insurance_coverage"
    elif "bca" in text:
        field = "bca_registration_info"
    elif re.search(r"licen[cs]e|registration|certif", text):
        field = "relevant_licences" if "licen" in text else "registrations"
    elif re.search(r"similar|experience|project", text):
        field = "past_project_experience"
    elif "employee" in text or "staff" in text:
        field = "number_of_employees"
    elif "location" in text or "geographic" in text:
        field = "geographic_coverage"
    else:
        field = "main_services"
    value = getattr(profile, field, None)
    if value is None or str(value).strip() == "":
        return field, None, "This company profile field has not been provided."
    return field, str(value), f"Compared against the company profile field '{field}'."


def _match(requirement: TenderRequirement, profile: CompanyProfile) -> tuple[str, str, str | None, str | None, str]:
    field, value, explanation = _company_evidence(profile, requirement)
    if not value:
        return "UNKNOWN", "LOW", field, value, explanation
    if _negative(value):
        return "NO_MATCH", "HIGH", field, value, "The company profile explicitly indicates that this capability or qualification is absent."
    requirement_terms = _tokens(requirement.requirement)
    company_terms = _tokens(value)
    overlap = requirement_terms & company_terms
    if requirement.category == "Eligibility" or requirement.mandatory:
        if overlap or (field == "past_project_experience" and len(value) > 20):
            return "MATCH", "HIGH", field, value, explanation
        return "PARTIAL_MATCH", "MEDIUM", field, value, "The company provided related information, but the profile does not explicitly confirm the complete tender requirement."
    if overlap:
        return "MATCH", "MEDIUM", field, value, explanation
    return "PARTIAL_MATCH", "LOW", field, value, "The company profile may be relevant, but the available text does not directly confirm the requirement."


def assess_tender(db: Session, tender: Tender) -> TenderAssessment:
    if tender.workspace_id is None:
        raise ValueError("Tender is not assigned to a workspace.")
    profile = get_or_create_profile(db, tender.workspace_id)
    latest = db.scalar(select(TenderAssessment).where(TenderAssessment.tender_id == tender.id).order_by(TenderAssessment.created_at.desc()))
    latest_evidence = db.scalar(select(CompanyEvidenceDocument.updated_at).where(CompanyEvidenceDocument.workspace_id == tender.workspace_id).order_by(CompanyEvidenceDocument.updated_at.desc()))
    if latest and latest.company_profile_version == profile.version and (latest_evidence is None or latest_evidence <= latest.created_at):
        return latest
    requirements = list(db.scalars(select(TenderRequirement).where(TenderRequirement.tender_id == tender.id)))
    results = [_match(requirement, profile) for requirement in requirements]
    counts = {status: sum(result[0] == status for result in results) for status in ("MATCH", "PARTIAL_MATCH", "NO_MATCH", "UNKNOWN")}
    critical_blockers = [requirement for requirement, result in zip(requirements, results) if requirement.mandatory and result[0] == "NO_MATCH"]
    has_profile_data = any(getattr(profile, field) not in (None, "") for field in PROFILE_FIELDS)
    if critical_blockers:
        recommendation, readiness, confidence = "NO_BID", 0, "HIGH"
    elif not has_profile_data or (requirements and counts["UNKNOWN"] == len(requirements)):
        recommendation, readiness, confidence = "INSUFFICIENT_INFORMATION", None, "LOW"
    elif requirements and counts["UNKNOWN"] == 0 and counts["PARTIAL_MATCH"] == 0:
        recommendation, readiness, confidence = "BID", 100, "MEDIUM"
    elif counts["MATCH"]:
        recommendation, readiness, confidence = "BID_WITH_REVIEW", round((counts["MATCH"] + counts["PARTIAL_MATCH"] * 0.5) / max(len(results), 1) * 100), "MEDIUM"
    else:
        recommendation, readiness, confidence = "REVIEW_REQUIRED", None, "LOW"
    explanation = "Readiness is an internal decision-support indicator based only on explicit profile fields and tender requirements; it is not a qualification determination."
    assessment = TenderAssessment(tender_id=tender.id, company_profile_version=profile.version, recommendation=recommendation, confidence=confidence, readiness_score=readiness, explanation=explanation, result_json=json.dumps({"counts": counts, "critical_blockers": [item.requirement for item in critical_blockers]}))
    db.add(assessment)
    db.flush()
    for requirement, result in zip(requirements, results):
        status, match_confidence, field, value, match_explanation = result
        evidence = link_requirement_evidence(db, requirement.id, requirement.requirement, tender.workspace_id)
        evidence_summary = "; ".join(f"{item['filename']} p.{item['page']}" for item in evidence[:3]) or None
        if evidence and status == "UNKNOWN":
            status, match_confidence = "PARTIAL_MATCH", "MEDIUM"
            match_explanation = "Supporting company document excerpts were found, but they do not by themselves prove the complete tender requirement."
        db.add(TenderRequirementMatch(assessment_id=assessment.id, tender_id=tender.id, requirement_id=requirement.id, match_status=status, confidence=match_confidence, company_field=field, company_value=value, company_explanation=match_explanation, critical=requirement.mandatory, evidence_document_count=len({item['document_id'] for item in evidence}), evidence_summary=evidence_summary))
        if status in {"UNKNOWN", "PARTIAL_MATCH", "NO_MATCH"}:
            title = f"Verify company information: {requirement.requirement}"
            db.add(TenderBidAction(assessment_id=assessment.id, tender_id=tender.id, title=title, reason=match_explanation, priority="HIGH" if requirement.mandatory else "MEDIUM", source_page=requirement.source_page, source_snippet=requirement.source_snippet, company_field=field))
    db.commit()
    return assessment