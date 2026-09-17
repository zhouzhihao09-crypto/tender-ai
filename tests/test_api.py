from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pathlib import Path

from backend.app.config import settings
from backend.app.main import app
from tests.test_pdf_service import make_text_pdf


client = TestClient(app)


def test_health_and_frontend_are_available() -> None:
    assert client.get("/health").json() == {"status": "ok", "service": "Tender AI"}
    assert client.get("/").status_code == 200


def test_non_pdf_upload_is_rejected() -> None:
    response = client.post(
        "/api/tenders",
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Only PDF files are supported."


def test_pdf_upload_creates_queued_tender() -> None:
    response = client.post(
        "/api/tenders",
        files={"file": ("office-renovation.pdf", b"%PDF-1.7 demo", "application/pdf")},
    )
    assert response.status_code == 201
    tender = response.json()
    assert tender["title"] == "office renovation"
    assert tender["analysis_status"] == "QUEUED"
    assert client.get(f"/api/tenders/{tender['id']}").status_code == 200


def test_real_pdf_upload_completes_analysis_and_supports_ask() -> None:
    response = client.post(
        "/api/tenders",
        files={"file": ("office-tender.pdf", make_text_pdf(["Reference: OR-2026. Submission deadline: 30 September 2026."]), "application/pdf")},
    )
    assert response.status_code == 201
    tender_id = response.json()["id"]
    insight = client.get(f"/api/tenders/{tender_id}/insight")
    assert insight.status_code == 200
    assert insight.json()["tender"]["analysis_status"] == "COMPLETED"
    answer = client.post(f"/api/tenders/{tender_id}/ask", params={"question": "What is the submission deadline?"})
    assert answer.status_code == 200
    assert answer.json()["sources"]
    reopened = TestClient(app).get(f"/api/tenders/{tender_id}/insight")
    assert reopened.status_code == 200
    assert reopened.json()["tender"]["analysis_status"] == "COMPLETED"
    assert any(settings.index_dir.glob(f"tender-{tender_id}.json"))


def test_real_tender_answers_requested_questions_with_page_sources() -> None:
    pages = [
        "Tender closing date: 30 September 2026 at 5:30 PM. Clarification deadline: 20 September 2026.",
        "Mandatory requirements include a completed BOQ and company profile. Documents required: insurance certificate. Main risks include late submission and missing insurance evidence.",
        "Project scope: Facilities maintenance at the civic centre. Tender validity period: 90 days. Failure to submit the signed form will automatically disqualify the bid.",
    ]
    response = client.post("/api/tenders", files={"file": ("sca-style-tender.pdf", make_text_pdf(pages), "application/pdf")})
    tender_id = response.json()["id"]
    questions = [
        "What is the tender closing date and time?",
        "What are the mandatory requirements?",
        "What documents do I need to prepare?",
        "What are the main risks in this tender?",
        "Will failure to provide a listed requirement automatically disqualify my bid?",
        "What is the clarification deadline?",
    ]
    for question in questions:
        answer = client.post(f"/api/tenders/{tender_id}/ask", params={"question": question})
        assert answer.status_code == 200
        assert answer.json()["sources"]
        assert any(source["page"] in {1, 2, 3} for source in answer.json()["sources"])


def test_empty_pdf_fails_cleanly_and_retry_remains_failed() -> None:
    writer = PdfWriter()
    import io
    buffer = io.BytesIO()
    writer.write(buffer)
    response = client.post("/api/tenders", files={"file": ("scanned.pdf", buffer.getvalue(), "application/pdf")})
    tender_id = response.json()["id"]
    insight = client.get(f"/api/tenders/{tender_id}/insight").json()
    assert insight["tender"]["analysis_status"] == "FAILED"
    assert "scanned" in insight["tender"]["analysis_error"].lower()
    retry = client.post(f"/api/tenders/{tender_id}/retry")
    assert retry.status_code == 200
    assert client.get(f"/api/tenders/{tender_id}/insight").json()["tender"]["analysis_status"] == "FAILED"


def test_ask_ai_before_analysis_is_complete_is_blocked() -> None:
    response = client.post("/api/tenders", files={"file": ("not-readable.pdf", b"%PDF-1.7 invalid", "application/pdf")})
    tender_id = response.json()["id"]
    answer = client.post(f"/api/tenders/{tender_id}/ask", params={"question": "What is the deadline?"})
    assert answer.status_code == 200
    assert answer.json()["sources"] == []
    assert "not complete" in answer.json()["answer"]


def test_unsupported_consequence_question_returns_no_evidence() -> None:
    response = client.post("/api/tenders", files={"file": ("limited-tender.pdf", make_text_pdf(["The bidder must submit a company profile."]), "application/pdf")})
    tender_id = response.json()["id"]
    answer = client.post(f"/api/tenders/{tender_id}/ask", params={"question": "Will failure to provide this automatically disqualify my bid?"})
    assert answer.status_code == 200
    assert answer.json()["sources"] == []
    assert "couldn't find" in answer.json()["answer"]


def test_dashboard_and_checklist_are_evidence_linked() -> None:
    pages = [
        "Tender closing date: 30 September 2026 at 5:30 PM.",
        "The bidder must submit a completed BOQ and company profile. Insurance certificate is required.",
    ]
    response = client.post("/api/tenders", files={"file": ("checklist-tender.pdf", make_text_pdf(pages), "application/pdf")})
    tender_id = response.json()["id"]
    insight = client.get(f"/api/tenders/{tender_id}/insight").json()
    checklist = insight["checklist"]
    assert checklist
    assert all(item["status"] == "NOT_STARTED" for item in checklist)
    assert all(item["source_page"] is not None and item["source_snippet"] for item in checklist)
    item_id = checklist[0]["id"]
    updated = client.patch(f"/api/checklist/{item_id}", json={"status": "IN_PROGRESS"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "IN_PROGRESS"
    invalid = client.patch(f"/api/checklist/{item_id}", json={"status": "READY"})
    assert invalid.status_code == 400
    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    row = next(item for item in dashboard.json()["tenders"] if item["id"] == tender_id)
    assert row["checklist_total"] == len(checklist)
    assert row["mandatory_requirements"] >= 1


def test_reanalyze_replaces_analysis_without_duplicate_tender() -> None:
    response = client.post("/api/tenders", files={"file": ("reanalyze.pdf", make_text_pdf(["The bidder must submit a completed BOQ."]), "application/pdf")})
    tender_id = response.json()["id"]
    item_id = client.get(f"/api/tenders/{tender_id}/checklist").json()[0]["id"]
    assert client.patch(f"/api/checklist/{item_id}", json={"status": "DONE"}).status_code == 200
    before = len(client.get("/api/tenders").json())
    reanalyze = client.post(f"/api/tenders/{tender_id}/reanalyze")
    assert reanalyze.status_code == 200
    assert len(client.get("/api/tenders").json()) == before
    assert client.get(f"/api/tenders/{tender_id}/insight").json()["tender"]["analysis_status"] == "COMPLETED"
    assert client.get(f"/api/tenders/{tender_id}/checklist").json()[0]["status"] == "DONE"