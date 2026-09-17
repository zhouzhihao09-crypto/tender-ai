from fastapi.testclient import TestClient

from backend.app.main import app
from tests.test_pdf_service import make_text_pdf


client = TestClient(app)


def create_workspace_tender() -> int:
    pages = [
        "Tender issue date: 01 September 2026. Clarification deadline: 20 September 2026. Submission deadline: 30 September 2026 at 5:30 PM.",
        "The bidder must submit a completed BOQ and company profile. Insurance documentation is required.",
    ]
    return client.post("/api/tenders", files={"file": ("bid-workspace.pdf", make_text_pdf(pages), "application/pdf")}).json()["id"]


def test_bid_workspace_is_created_from_existing_tender_data() -> None:
    tender_id = create_workspace_tender()
    workspace = client.get(f"/api/tenders/{tender_id}/bid-workspace")
    assert workspace.status_code == 200
    data = workspace.json()
    assert data["requirements"]
    assert data["documents"]
    assert data["clarifications"]
    assert data["timeline"][0]["event"] == "Tender Issue Date"
    assert data["timeline"][-1]["event"] == "Submission Deadline"
    assert data["readiness"]["state"] == "NOT_READY"
    assert client.get(f"/api/tenders/{tender_id}/bid-workspace").json()["id"] == data["id"]


def test_bid_decision_and_status_persist_separately() -> None:
    tender_id = create_workspace_tender()
    assert client.patch(f"/api/tenders/{tender_id}/bid-workspace/status", json={"workflow_status": "PREPARING"}).json()["workflow_status"] == "PREPARING"
    decision = client.patch(f"/api/tenders/{tender_id}/bid-workspace/decision", json={"decision": "BID", "note": "Proceed subject to insurance review."}).json()
    assert decision["decision"] == "BID"
    assert decision["decision_note"] == "Proceed subject to insurance review."


def test_bid_requirement_document_link_and_final_readiness() -> None:
    tender_id = create_workspace_tender()
    workspace = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()
    evidence = client.post("/api/company-evidence", files={"file": ("supporting.pdf", make_text_pdf(["Completed BOQ and public liability insurance evidence."]), "application/pdf")}).json()
    document = workspace["documents"][0]
    link = client.post(f"/api/bid-documents/{document['id']}/evidence", json={"evidence_document_id": evidence["id"]})
    assert link.status_code == 200
    for item in workspace["documents"]:
        assert client.patch(f"/api/bid-documents/{item['id']}", json={"status": "READY"}).status_code == 200
    for requirement in workspace["requirements"]:
        assert client.patch(f"/api/bid-requirements/{requirement['id']}", json={"status": "READY"}).status_code == 200
    for clarification in workspace["clarifications"]:
        assert client.patch(f"/api/bid-clarifications/{clarification['id']}", json={"status": "CLOSED"}).status_code == 200
    refreshed = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()
    assert refreshed["documents"][0]["evidence_document_ids"] == [evidence["id"]]
    assert refreshed["readiness"]["state"] in {"READY", "ALMOST_READY"}
    assert client.patch(f"/api/bid-documents/{document['id']}", json={"status": "INVALID"}).status_code == 400


def test_bid_notes_and_clarification_updates_are_local_and_persisted() -> None:
    tender_id = client.post("/api/tenders", files={"file": ("clarify.pdf", make_text_pdf(["Insurance documentation is required."]), "application/pdf")}).json()["id"]
    workspace = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()
    note = client.post(f"/api/tenders/{tender_id}/bid-notes", json={"scope": "TENDER", "body": "Internal review scheduled."})
    assert note.status_code == 200
    if workspace["clarifications"]:
        clarification = workspace["clarifications"][0]
        updated = client.patch(f"/api/bid-clarifications/{clarification['id']}", json={"question": "Please confirm insurance limit.", "status": "READY_TO_SEND"})
        assert updated.status_code == 200
        assert updated.json()["status"] == "READY_TO_SEND"


def test_one_company_document_can_be_reused_in_multiple_bid_packages() -> None:
    evidence_id = client.post("/api/company-evidence", files={"file": ("reusable-insurance.pdf", make_text_pdf(["Insurance certificate evidence."]), "application/pdf")}).json()["id"]
    first_tender = create_workspace_tender()
    second_tender = create_workspace_tender()
    first_document = client.get(f"/api/tenders/{first_tender}/bid-workspace").json()["documents"][0]
    second_document = client.get(f"/api/tenders/{second_tender}/bid-workspace").json()["documents"][0]
    assert client.post(f"/api/bid-documents/{first_document['id']}/evidence", json={"evidence_document_id": evidence_id}).status_code == 200
    assert client.post(f"/api/bid-documents/{second_document['id']}/evidence", json={"evidence_document_id": evidence_id}).status_code == 200
    assert evidence_id in client.get(f"/api/tenders/{first_tender}/bid-workspace").json()["documents"][0]["evidence_document_ids"]
    assert evidence_id in client.get(f"/api/tenders/{second_tender}/bid-workspace").json()["documents"][0]["evidence_document_ids"]
