import io
import json
import zipfile

from fastapi.testclient import TestClient

from backend.app.main import app
from tests.test_bid_workspace import create_workspace_tender
from tests.test_pdf_service import make_text_pdf


client = TestClient(app)


def test_package_creation_reuse_and_export_blocking() -> None:
    tender_id = create_workspace_tender()
    first = client.get(f"/api/tenders/{tender_id}/bid-package").json()
    second = client.get(f"/api/tenders/{tender_id}/bid-package").json()
    assert first["id"] == second["id"]
    assert first["items"]
    blocked = client.post(f"/api/tenders/{tender_id}/bid-package/export", json={})
    assert blocked.status_code == 409
    assert "Export blocked" in blocked.json()["detail"]


def test_package_item_order_inclusion_preview_and_safe_export() -> None:
    tender_id = create_workspace_tender()
    workspace = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()
    evidence = client.post("/api/company-evidence", files={"file": ("../supporting evidence.pdf", make_text_pdf(["Completed BOQ supporting evidence."]), "application/pdf")}).json()
    package = client.get(f"/api/tenders/{tender_id}/bid-package").json()
    add = client.post(f"/api/tenders/{tender_id}/bid-package/evidence", json={"evidence_document_id": evidence["id"], "bid_document_id": workspace["documents"][0]["id"]})
    assert add.status_code == 200
    package = client.get(f"/api/tenders/{tender_id}/bid-package").json()
    evidence_item = next(item for item in package["items"] if item["evidence_document_id"] == evidence["id"])
    assert client.patch(f"/api/bid-package-items/{evidence_item['id']}", json={"status": "READY", "display_order": 0, "notes": "Reviewed locally."}).status_code == 200
    preview = client.get(f"/api/company-evidence/{evidence['id']}/preview", params={"page": 1})
    assert preview.status_code == 200
    assert "Completed BOQ" in preview.json()["text"]
    for item in workspace["requirements"]:
        client.patch(f"/api/bid-requirements/{item['id']}", json={"status": "READY"})
    for item in workspace["documents"]:
        client.patch(f"/api/bid-documents/{item['id']}", json={"status": "READY"})
    for item in workspace["clarifications"]:
        client.patch(f"/api/bid-clarifications/{item['id']}", json={"status": "CLOSED"})
    for item in package["items"]:
        client.patch(f"/api/bid-package-items/{item['id']}", json={"status": "READY"})
    exported = client.post(f"/api/tenders/{tender_id}/bid-package/export", json={})
    assert exported.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(exported.content))
    assert "submission_manifest.json" in archive.namelist()
    manifest = json.loads(archive.read("submission_manifest.json"))
    assert manifest["tender"]
    assert "5:30 PM" in manifest["closing"]
    assert all(".." not in name for name in archive.namelist())
    history = client.get(f"/api/tenders/{tender_id}/bid-package").json()["exports"]
    assert history and history[0]["included_count"] >= 1


def test_evidence_can_be_added_to_multiple_packages_without_copying() -> None:
    evidence_id = client.post("/api/company-evidence", files={"file": ("shared.pdf", make_text_pdf(["Shared certificate evidence."]), "application/pdf")}).json()["id"]
    first, second = create_workspace_tender(), create_workspace_tender()
    assert client.post(f"/api/tenders/{first}/bid-package/evidence", json={"evidence_document_id": evidence_id}).status_code == 200
    assert client.post(f"/api/tenders/{second}/bid-package/evidence", json={"evidence_document_id": evidence_id}).status_code == 200
    assert any(item["evidence_document_id"] == evidence_id for item in client.get(f"/api/tenders/{first}/bid-package").json()["items"])
    assert any(item["evidence_document_id"] == evidence_id for item in client.get(f"/api/tenders/{second}/bid-package").json()["items"])


def test_duplicate_evidence_link_is_not_duplicated_within_a_package() -> None:
    evidence_id = client.post("/api/company-evidence", files={"file": ("once.pdf", make_text_pdf(["Reusable company evidence document." ]), "application/pdf")}).json()["id"]
    tender_id = create_workspace_tender()
    body = {"evidence_document_id": evidence_id}
    first = client.post(f"/api/tenders/{tender_id}/bid-package/evidence", json=body).json()
    second = client.post(f"/api/tenders/{tender_id}/bid-package/evidence", json=body).json()
    assert first["id"] == second["id"]
