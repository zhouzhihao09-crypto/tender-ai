"""Phase 8 productization tests: overview aggregation, blockers, evidence picker, clarifications, package blockers, frontend wiring."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from tests.test_bid_workspace import create_workspace_tender
from tests.test_pdf_service import make_text_pdf

client = TestClient(app)
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


def test_overview_aggregates_actions_and_library_rows() -> None:
    tender_id = create_workspace_tender()
    overview = client.get("/api/overview").json()
    assert overview["totals"]["tenders"] >= 1
    row = next(row for row in overview["tenders"] if row["id"] == tender_id)
    for key in ("title", "reference", "organization", "closing", "analysis_status", "workflow_status", "decision", "recommendation", "readiness_state", "blocker_count", "blockers", "updated_at"):
        assert key in row
    assert row["analysis_status"] == "COMPLETED"
    assert row["workflow_status"] == "BID_DECISION_PENDING"
    assert row["decision"] == "PENDING"
    assert row["blocker_count"] >= 1
    assert overview["actions"]["awaiting_decision"]["count"] >= 1
    assert tender_id in overview["actions"]["blocked"]["tender_ids"]
    assert overview["actions"]["missing_documents"]["count"] >= 1
    assert tender_id not in overview["actions"]["needs_attention"]["tender_ids"]
    assert all("days_remaining" in deadline for deadline in overview["deadlines"])


def test_blockers_are_deterministic_and_clear_when_items_are_ready() -> None:
    tender_id = create_workspace_tender()
    workspace = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()
    kinds = {blocker["kind"] for blocker in workspace["readiness"]["blockers"]}
    assert {"requirement", "document", "clarification"} <= kinds
    assert any(blocker["kind"] == "decision" for blocker in workspace["readiness"]["attention"])
    assert workspace["readiness"]["state"] == "NOT_READY"
    for item in workspace["requirements"]:
        assert client.patch(f"/api/bid-requirements/{item['id']}", json={"status": "READY"}).status_code == 200
    for item in workspace["documents"]:
        assert client.patch(f"/api/bid-documents/{item['id']}", json={"status": "READY"}).status_code == 200
    for item in workspace["clarifications"]:
        assert client.patch(f"/api/bid-clarifications/{item['id']}", json={"status": "CLOSED"}).status_code == 200
    client.patch(f"/api/tenders/{tender_id}/bid-workspace/decision", json={"decision": "BID", "note": "Proceed."})
    refreshed = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()
    assert refreshed["readiness"]["state"] == "READY"
    assert refreshed["readiness"]["blockers"] == []
    assert refreshed["readiness"]["attention"] == []
    assert "Proceed." in refreshed["decision_note"]
def test_clarification_answer_persists_without_sending_anything() -> None:
    tender_id = create_workspace_tender()
    workspace = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()
    assert workspace["clarifications"], "fixture tender should surface one insurance clarification"
    clarification = workspace["clarifications"][0]
    updated = client.patch(f"/api/bid-clarifications/{clarification['id']}", json={"status": "READY_TO_SEND"})
    assert updated.status_code == 200
    answered = client.patch(f"/api/bid-clarifications/{clarification['id']}", json={"answer": "Coverage of 1,000,000 SGD confirmed by buyer.", "status": "ANSWERED"})
    assert answered.status_code == 200
    assert answered.json()["answer"] == "Coverage of 1,000,000 SGD confirmed by buyer."
    refreshed = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()
    row = next(item for item in refreshed["clarifications"] if item["id"] == clarification["id"])
    assert row["answer"] == "Coverage of 1,000,000 SGD confirmed by buyer."
    assert row["status"] == "ANSWERED"


def test_evidence_suggestions_are_conservative_and_never_link_automatically() -> None:
    tender_id = create_workspace_tender()
    workspace = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()
    before = {document["id"]: list(document["evidence_document_ids"]) for document in workspace["documents"]}
    evidence = client.post(
        "/api/company-evidence",
        files={"file": ("boq-support.pdf", make_text_pdf(["Completed BOQ for facilities works with total cost in SGD and cost breakdown."]), "application/pdf")},
        data={"category": "TECHNICAL", "description": "BOQ evidence"},
    ).json()
    produced = []
    for document in workspace["documents"]:
        payload = client.get(f"/api/tenders/{tender_id}/bid-documents/{document['id']}/evidence-suggestions").json()
        assert payload["requested_document"]
        produced.extend(payload["suggestions"])
    assert produced, "lexical suggestions should find matching evidence for at least one requested document"
    for suggestion in produced:
        assert suggestion["relevance"] == "POTENTIALLY_RELEVANT"
        assert suggestion["review_required"] is True
    boq_suggestions = client.get(f"/api/tenders/{tender_id}/bid-documents/{workspace['documents'][0]['id']}/evidence-suggestions").json()["suggestions"]
    assert any(suggestion["document_id"] == evidence["id"] for suggestion in boq_suggestions)
    after_suggestions = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()
    assert {document["id"]: list(document["evidence_document_ids"]) for document in after_suggestions["documents"]} == before
    bid_document_id = workspace["documents"][0]["id"]
    assert client.post(f"/api/bid-documents/{bid_document_id}/evidence", json={"evidence_document_id": evidence["id"]}).status_code == 200
    linked = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()["documents"][0]["evidence_document_ids"]
    assert evidence["id"] in linked
    assert client.delete(f"/api/bid-documents/{bid_document_id}/evidence/{evidence['id']}").status_code == 204
    unlinked = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()["documents"][0]["evidence_document_ids"]
    assert evidence["id"] not in unlinked
    assert client.delete(f"/api/bid-documents/{bid_document_id}/evidence/{evidence['id']}").status_code == 404
def test_package_payload_reports_structured_blockers_and_export_is_gated() -> None:
    tender_id = create_workspace_tender()
    package = client.get(f"/api/tenders/{tender_id}/bid-package").json()
    assert package["items"]
    assert any(blocker["kind"] == "package_item" for blocker in package["blockers"])
    assert any(blocker["kind"] == "workspace" for blocker in package["blockers"])
    assert package["readiness"]["state"] in {"NOT_READY", "REVIEW_REQUIRED"}
    assert package["exports"] == []
    blocked = client.post(f"/api/tenders/{tender_id}/bid-package/export", json={})
    assert blocked.status_code == 409
    assert "Export blocked" in blocked.json()["detail"]


def test_phase8_frontend_navigation_wiring_and_stylesheets_exist() -> None:
    html = client.get("/").text
    for marker in ('data-view="dashboard"', 'data-view="tenders"', 'data-view="tasks"', 'data-view="vault"', 'data-view="profile"', 'id="blockers-host"', 'id="bid-workspace-host"', 'id="package-host"', 'id="company-fit-content"', 'id="picker-modal"', 'id="preview-modal"', "/phase8.css", "/phase9.css", 'src="/app.js', 'src="/workspace.js"'):
        assert marker in html, marker
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    workspace_js = (FRONTEND_DIR / "workspace.js").read_text(encoding="utf-8")
    for snippet in ("/api/overview", "/api/company-evidence", "/api/tenders", "/api/company-profile"):
        assert snippet in app_js, snippet
    for snippet in ("evidence-suggestions", "/api/bid-package-items", "/api/tenders/", "bid-package/export", "function loadBidPackage", "function bindPackageControls"):
        assert snippet in workspace_js, snippet
    for stylesheet in re.findall(r'href="/([^"]+\.css)"', html):
        assert (FRONTEND_DIR / stylesheet).is_file(), stylesheet
    for script in re.findall(r'src="/([^"]+\.js)"', html):
        assert (FRONTEND_DIR / script).is_file(), script


