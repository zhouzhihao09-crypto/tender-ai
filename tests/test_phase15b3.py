"""Phase 15B-3: ObjectStorage temporary-file cleanup regression tests."""

import io
import json
import zipfile
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models import BidPackage
from backend.app.services.package_service import export_package
from backend.app.database import SessionLocal
from backend.app.storage import (
    LocalFileStorage,
    ObjectStorage,
    get_storage_backend,
)


def create_workspace_tender() -> int:
    """Create a workspace tender via the public upload endpoint."""
    from tests.test_bid_workspace import create_workspace_tender as _create

    return _create()


class _Bytes:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self):
        return self._data


class _StubS3:
    """Minimal S3 client stub used by the S3-backed storage tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def get_object(self, **kwargs):
        return {"Body": _Bytes(self.objects.get(kwargs["Key"], b""))}

    def head_object(self, **kwargs):
        return {}

    def delete_object(self, **kwargs):
        self.objects.pop(kwargs["Key"], None)


def _s3_storage(monkeypatch, tmp_path) -> ObjectStorage:
    """Return an ObjectStorage backed by a stub S3 client."""
    stub = _StubS3()
    monkeypatch.setattr(boto3, "client", lambda service, **kwargs: stub)
    storage = get_storage_backend("s3", bucket="phase15b3-bucket", region="us-east-1")
    # Pre-seed the object so path()/materialize() have something to download.
    stub.objects["report.pdf"] = b"report content"
    return storage


# ---------------------------------------------------------------------------
# Cleanup after successful use
# ---------------------------------------------------------------------------


def test_temp_file_cleaned_after_successful_materialize(monkeypatch, tmp_path) -> None:
    storage = _s3_storage(monkeypatch, tmp_path)
    with storage.materialize("report.pdf") as path:
        assert path.exists()
        assert str(path) in storage.temp_files
    assert str(path) not in storage.temp_files
    assert not path.exists()


def test_temp_file_cleaned_when_consuming_operation_raises(monkeypatch, tmp_path) -> None:
    storage = _s3_storage(monkeypatch, tmp_path)

    class BoomError(Exception):
        pass

    try:
        with storage.materialize("report.pdf") as path:
            assert path.exists()
            raise BoomError("consume failed")
    except BoomError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected BoomError to propagate")

    assert str(path) not in storage.temp_files
    assert not path.exists()


# ---------------------------------------------------------------------------
# Persistent files are NOT deleted by temporary cleanup
# ---------------------------------------------------------------------------


def test_local_persistent_path_release_is_noop(tmp_path) -> None:
    storage = LocalFileStorage(tmp_path / "local")
    storage.save("keep.pdf", b"persistent")
    path = storage.path("keep.pdf")
    assert path.exists()
    storage.release_path(path)
    assert path.exists()


def test_local_materialize_does_not_delete_persistent_file(tmp_path) -> None:
    storage = LocalFileStorage(tmp_path / "local")
    storage.save("keep.pdf", b"persistent")
    with storage.materialize("keep.pdf") as path:
        assert path.exists()
    assert path.exists()


# ---------------------------------------------------------------------------
# S3 materialization cleanup does not delete the underlying S3 object
# ---------------------------------------------------------------------------


def test_s3_materialize_cleanup_keeps_s3_object(monkeypatch, tmp_path) -> None:
    storage = _s3_storage(monkeypatch, tmp_path)
    with storage.materialize("report.pdf") as path:
        assert path.exists()
    assert not path.exists()
    # The S3 object must still be readable after cleanup.
    assert storage.read("report.pdf") == b"report content"


def test_release_path_is_safe_for_untracked_paths(tmp_path) -> None:
    storage = LocalFileStorage(tmp_path / "local")
    storage.save("keep.pdf", b"persistent")
    # Releasing a path this backend never created is a no-op.
    storage.release_path(storage.path("keep.pdf"))
    assert storage.path("keep.pdf").exists()


# ---------------------------------------------------------------------------
# Concurrency / collision safety
# ---------------------------------------------------------------------------


def test_concurrent_temporary_paths_do_not_collide(monkeypatch, tmp_path) -> None:
    storage = _s3_storage(monkeypatch, tmp_path)
    first = storage.writable_path("package.zip")
    second = storage.writable_path("package.zip")
    assert first != second
    assert str(first) in storage.temp_files
    assert str(second) in storage.temp_files
    storage.release_path(first)
    assert str(first) not in storage.temp_files
    assert str(second) in storage.temp_files
    storage.release_path(second)
    assert str(second) not in storage.temp_files


def test_releasing_one_temp_does_not_remove_another(monkeypatch, tmp_path) -> None:
    storage = _s3_storage(monkeypatch, tmp_path)
    a = storage.writable_path("a.zip")
    b = storage.writable_path("b.zip")
    storage.release_path(a)
    assert not a.exists()
    assert b.exists()
    assert str(b) in storage.temp_files


# ---------------------------------------------------------------------------
# Path-traversal protections remain intact
# ---------------------------------------------------------------------------


def test_local_path_traversal_still_blocked(tmp_path) -> None:
    storage = LocalFileStorage(tmp_path / "local")
    with pytest.raises(ValueError):
        storage.path("../outside.pdf")


def test_s3_path_traversal_still_blocked(monkeypatch, tmp_path) -> None:
    storage = _s3_storage(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        storage.path("../outside.pdf")


# ---------------------------------------------------------------------------
# ZIP/export still works and the returned artifact is valid
# ---------------------------------------------------------------------------


def test_s3_export_build_artifact_is_cleaned_by_release_path(monkeypatch, tmp_path) -> None:
    """export_package builds a valid ZIP on an S3 backend; the build
    artifact is temporary and must be released by ``release_path``
    without affecting the persisted export."""
    import backend.app.services.package_service as pkg
    import backend.app.storage as storage_module
    from tests.test_pdf_service import make_text_pdf

    s3_export = _s3_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(pkg, "export_storage", s3_export)
    monkeypatch.setattr(storage_module, "export_storage", s3_export)

    client = TestClient(app)
    tender_id = create_workspace_tender()
    workspace = client.get(f"/api/tenders/{tender_id}/bid-workspace").json()
    evidence = client.post(
        "/api/company-evidence",
        files={"file": ("supporting.pdf", make_text_pdf(["Completed BOQ supporting evidence."]), "application/pdf")},
    ).json()
    package = client.get(f"/api/tenders/{tender_id}/bid-package").json()
    client.post(
        f"/api/tenders/{tender_id}/bid-package/evidence",
        json={"evidence_document_id": evidence["id"], "bid_document_id": workspace["documents"][0]["id"]},
    )
    for item in workspace["requirements"]:
        client.patch(f"/api/bid-requirements/{item['id']}", json={"status": "READY"})
    for item in workspace["documents"]:
        client.patch(f"/api/bid-documents/{item['id']}", json={"status": "READY"})
    for item in package["items"]:
        client.patch(f"/api/bid-package-items/{item['id']}", json={"status": "READY"})

    with SessionLocal() as db:
        package_obj = db.get(BidPackage, package["id"])
        output, payload = export_package(db, package_obj, allow_incomplete=True)

    archive = zipfile.ZipFile(io.BytesIO(output.read_bytes()))
    assert "submission_manifest.json" in archive.namelist()
    manifest = json.loads(archive.read("submission_manifest.json"))
    assert manifest["tender"]
    assert "5:30 PM" in manifest["closing"]

    # The build artifact is temporary on S3; release it.
    s3_export.release_path(output)
    assert not output.exists()
    assert str(output) not in s3_export.temp_files
    # The persisted export is still readable from S3.
    assert s3_export.exists(output.name)