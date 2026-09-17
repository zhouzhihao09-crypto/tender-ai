from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models import Tender, TenderRequirement
from backend.app.services.company_service import _match, get_or_create_profile
from backend.app.database import SessionLocal
from tests.test_pdf_service import make_text_pdf


client = TestClient(app)


def test_company_profile_is_singleton_and_updates_version() -> None:
    first = client.get("/api/company-profile").json()
    updated = client.put("/api/company-profile", json={"company_name": "Northstar Facilities", "industry": "Facilities", "past_project_experience": "Three similar facilities projects completed in 2024 and 2025.", "insurance_coverage": "Public liability insurance certificate available."})
    assert updated.status_code == 200
    assert updated.json()["company_name"] == "Northstar Facilities"
    assert updated.json()["version"] > first["version"]
    assert client.get("/api/company-profile").json()["id"] == first["id"]


def test_matching_classifies_match_partial_no_match_and_unknown() -> None:
    db = SessionLocal()
    profile = get_or_create_profile(db)
    profile.past_project_experience = "Three similar projects completed in 2024."
    profile.insurance_coverage = "Public liability insurance available."
    profile.bca_registration_info = "No BCA registration."
    db.commit()
    match = TenderRequirement(requirement="Similar project experience", category="Eligibility", mandatory=True)
    partial = TenderRequirement(requirement="Insurance coverage amount", category="Insurance", mandatory=True)
    no_match = TenderRequirement(requirement="BCA registration", category="Eligibility", mandatory=True)
    unknown = TenderRequirement(requirement="Required certification", category="Eligibility", mandatory=True)
    assert _match(match, profile)[0] == "MATCH"
    assert _match(partial, profile)[0] in {"MATCH", "PARTIAL_MATCH"}
    assert _match(no_match, profile)[0] == "NO_MATCH"
    assert _match(unknown, profile)[0] == "UNKNOWN"
    db.close()


def test_company_assessment_is_evidence_linked_and_conservative() -> None:
    client.put("/api/company-profile", json={"company_name": "Northstar Facilities", "past_project_experience": "Three similar facilities projects completed in 2024 and 2025."})
    pdf = make_text_pdf(["The bidder must submit a completed BOQ and provide evidence of similar project experience. Insurance documentation is required."])
    created = client.post("/api/tenders", files={"file": ("company-match.pdf", pdf, "application/pdf")})
    tender_id = created.json()["id"]
    assessment = client.post(f"/api/tenders/{tender_id}/company-assessment")
    assert assessment.status_code == 200
    result = assessment.json()
    assert result["recommendation"] in {"BID_WITH_REVIEW", "REVIEW_REQUIRED", "INSUFFICIENT_INFORMATION"}
    assert result["recommendation"] != "NO_BID"
    assert result["readiness_score"] is None or 0 <= result["readiness_score"] <= 100
    assert result["matches"]
    assert all(item["company_field"] for item in result["matches"])
    assert all(action["source_page"] is not None for action in result["actions"])
    if result["actions"]:
        action_update = client.patch(f"/api/company-actions/{result['actions'][0]['id']}", json={"status": "IN_PROGRESS"})
        assert action_update.status_code == 200
        assert action_update.json()["status"] == "IN_PROGRESS"
    assert client.get(f"/api/tenders/{tender_id}/company-assessment").status_code == 200


def test_explicit_company_blocker_can_produce_no_bid() -> None:
    client.put("/api/company-profile", json={"company_name": "Northstar Facilities", "bca_registration_info": "No BCA registration."})
    pdf = make_text_pdf(["BCA registration is mandatory for this tender."])
    tender_id = client.post("/api/tenders", files={"file": ("blocker.pdf", pdf, "application/pdf")}).json()["id"]
    result = client.post(f"/api/tenders/{tender_id}/company-assessment").json()
    assert result["recommendation"] == "NO_BID"
    assert result["readiness_score"] == 0
    assert any(item["match_status"] == "NO_MATCH" and item["critical"] for item in result["matches"])
