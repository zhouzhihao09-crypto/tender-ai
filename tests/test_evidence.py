from pathlib import Path
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from backend.app.main import app
from backend.app.config import settings
from tests.test_pdf_service import make_text_pdf


client = TestClient(app)


def test_evidence_upload_extracts_pages_and_expiry() -> None:
    content = make_text_pdf(["Project completion certificate for Northstar Facilities. Expiry date: 30 September 2027.", "Completed similar project in 2024."])
    response = client.post("/api/company-evidence", files={"file": ("project-reference.pdf", content, "application/pdf")}, data={"category": "PROJECT_REFERENCE", "description": "Completed project evidence"})
    assert response.status_code == 201
    document = response.json()
    assert document["category"] == "PROJECT_REFERENCE"
    assert document["page_count"] == 2
    assert document["expiry_date"] == "30 September 2027"
    assert (settings.company_evidence_dir / next(path.name for path in settings.company_evidence_dir.iterdir() if path.name.endswith(".pdf"))).exists()


def test_evidence_no_expiry_and_search_are_explicit() -> None:
    content = make_text_pdf(["Insurance certificate. Public liability coverage is provided."])
    response = client.post("/api/company-evidence", files={"file": ("insurance.pdf", content, "application/pdf")}, data={"category": "INSURANCE"})
    assert response.status_code == 201
    assert response.json()["expiry_date"] is None
    results = client.get("/api/company-evidence/search", params={"q": "public liability coverage"})
    assert results.status_code == 200
    assert results.json()
    assert results.json()[0]["page"] == 1
    assert "Public liability" in results.json()[0]["snippet"]


def test_empty_and_non_pdf_evidence_fail_safely() -> None:
    writer = PdfWriter()
    buffer = BytesIO()
    writer.write(buffer)
    empty = client.post("/api/company-evidence", files={"file": ("scan.pdf", buffer.getvalue(), "application/pdf")})
    assert empty.status_code == 400
    invalid = client.post("/api/company-evidence", files={"file": ("bad.txt", b"not pdf", "text/plain")})
    assert invalid.status_code == 400


def test_evidence_deletion_removes_searchable_document() -> None:
    content = make_text_pdf(["Unique technical capability evidence."])
    created = client.post("/api/company-evidence", files={"file": ("technical.pdf", content, "application/pdf")}).json()
    document_id = created["id"]
    assert client.delete(f"/api/company-evidence/{document_id}").status_code == 204
    assert all(result["document_id"] != document_id for result in client.get("/api/company-evidence/search", params={"q": "unique technical capability"}).json())


def test_requirement_assessment_records_company_document_evidence() -> None:
    client.put("/api/company-profile", json={"company_name": "Northstar", "past_project_experience": None})
    client.post("/api/company-evidence", files={"file": ("similar-project.pdf", make_text_pdf(["Similar project completed in 2024 for facilities maintenance."]), "application/pdf")}, data={"category": "PROJECT_REFERENCE"})
    tender_id = client.post("/api/tenders", files={"file": ("evidence-match.pdf", make_text_pdf(["The bidder must provide evidence of similar project experience."]), "application/pdf")}).json()["id"]
    result = client.post(f"/api/tenders/{tender_id}/company-assessment").json()
    assert result["matches"]
    assert result["matches"][0]["evidence_document_count"] >= 1
    assert result["matches"][0]["evidence_summary"]
