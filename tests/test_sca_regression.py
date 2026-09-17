"""SCA tender regression: protects the real-world validated extraction and conservative behaviour.

Uses the real SCA_tender.pdf when present and skips cleanly when it is absent.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.company_service import PROFILE_FIELDS

client = TestClient(app)
SCA_PDF = Path(__file__).resolve().parents[1] / "SCA_tender.pdf"

pytestmark = pytest.mark.skipif(not SCA_PDF.is_file(), reason="SCA_tender.pdf is not present")

_SCA_TENDER_ID: int | None = None


def _sca_tender_id() -> int:
    global _SCA_TENDER_ID
    if _SCA_TENDER_ID is None:
        response = client.post("/api/tenders", files={"file": ("SCA_tender.pdf", SCA_PDF.read_bytes(), "application/pdf")})
        assert response.status_code == 201, response.text
        _SCA_TENDER_ID = response.json()["id"]
    return _SCA_TENDER_ID


def _reset_company_profile() -> None:
    response = client.put("/api/company-profile", json={field: None for field in PROFILE_FIELDS})
    assert response.status_code == 200


def _sca_insight() -> dict:
    insight = client.get(f"/api/tenders/{_sca_tender_id()}/insight").json()
    assert insight["tender"]["analysis_status"] == "COMPLETED", insight["tender"]["analysis_error"]
    return insight


def test_sca_reference_closing_and_all_six_submission_requirements() -> None:
    insight = _sca_insight()
    summary = insight["analysis"]["summary"]
    assert summary["reference"] == "SCA/MAINCON/2025/01"
    dates = insight["analysis"]["dates"]
    closing = next(item for item in dates if "closing" in item["event"].lower() or "submission" in item["event"].lower())
    assert closing["date"] == "28/08/2025"
    assert "05:30" in (closing["time"] or "") and "PM" in (closing["time"] or "").upper()
    requirement_text = " ".join(f"{item['requirement']} {item.get('source_snippet') or ''}" for item in insight["requirements"]).lower()
    for needle in ("boq", "work programme", "subcontractor", "company profile", "similar project", "safety and risk assessment"):
        assert needle in requirement_text, needle
    insurance = [item for item in insight["requirements"] if "insurance" in item["requirement"].lower()]
    assert insurance and any(item["mandatory"] for item in insurance)
    clarification = [item for item in dates if "clarification" in item["event"].lower()]
    assert clarification and clarification[0]["date"] == "28/08/2025"
    if insight["tender"]["deadline"]:
        assert "2025-08-28" in insight["tender"]["deadline"]
def test_sca_company_assessment_is_conservative_and_missing_info_is_never_no_bid() -> None:
    _reset_company_profile()
    insight = _sca_insight()
    result = client.post(f"/api/tenders/{insight['tender']['id']}/company-assessment").json()
    assert result["recommendation"] in {"INSUFFICIENT_INFORMATION", "REVIEW_REQUIRED", "BID_WITH_REVIEW"}
    assert result["recommendation"] != "NO_BID"
    assert result["recommendation"] != "BID"
    assert result["matches"]
    statuses = {match["match_status"] for match in result["matches"]}
    assert "UNKNOWN" in statuses
    unknown = [match for match in result["matches"] if match["match_status"] == "UNKNOWN"]
    assert all(match["company_value"] is None for match in unknown)
    assert all("not" in match["company_explanation"].lower() or "unknown" in match["company_explanation"].lower() for match in unknown)


def test_sca_ask_ai_is_grounded_with_page_sources_and_stays_conservative() -> None:
    tender_id = _sca_tender_id()
    closing = client.post(f"/api/tenders/{tender_id}/ask", params={"question": "What is the tender closing date?"}).json()
    assert closing["sources"]
    assert any(source["page"] in {1, 2, 3, 4} for source in closing["sources"])
    assert "28/08/2025" in closing["answer"] or "05:30" in closing["answer"]
    documents = client.post(f"/api/tenders/{tender_id}/ask", params={"question": "What documents must I submit?"}).json()
    assert documents["sources"]
    requirements = client.post(f"/api/tenders/{tender_id}/ask", params={"question": "What are the tender submission requirements?"}).json()
    assert requirements["sources"]
    assert {source["page"] for source in requirements["sources"]} == {3}
    requirements_answer = requirements["answer"].lower()
    for expected in (
        "completed boq with total cost in singapore dollars (sgd)",
        "detailed work programme with timeline",
        "proposed subcontractors (if any) with qualifications",
        "company profile, including bca registration and relevant licences",
        "evidence of similar projects completed in the past 5 years",
        "safety and risk assessment plan (wsh-compliant)",
    ):
        assert expected in requirements_answer
    assert "invites tenders from qualified" not in requirements_answer
    for question in ("What are the evaluation weightings?", "What are the evaluation criteria?"):
        evaluation = client.post(f"/api/tenders/{tender_id}/ask", params={"question": question})
        assert evaluation.status_code == 200
        evaluation_payload = evaluation.json()
        assert evaluation_payload["sources"] == []
        evaluation_answer = evaluation_payload["answer"].lower()
        assert "not stated" in evaluation_answer or "no evidence" in evaluation_answer
        assert "invites tenders from qualified" not in evaluation_answer
        assert "costs incurred in preparing and submitting" not in evaluation_answer
    unsupported = client.post(f"/api/tenders/{tender_id}/ask", params={"question": "What are the evaluation weightings and scoring criteria?"}).json()
    # The tender states no weightings; the answer may retrieve unrelated grounded text
    # but must never invent weights, percentages, or a scoring scheme.
    assert not re.search(r"\d+\s*(?:%|percent|points?\b|weight)", unsupported["answer"], re.IGNORECASE)
    consequence = client.post(f"/api/tenders/{tender_id}/ask", params={"question": "Will failure to provide a listed requirement automatically disqualify my bid?"}).json()
    assert "disqualif" not in consequence["answer"].lower()

