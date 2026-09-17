import os
import subprocess
import sys

import boto3
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.main import app
from backend.app.services.ai_provider import OllamaProvider, get_llm_provider
from backend.app.storage import LocalFileStorage, ObjectStorage, get_storage_backend


def test_readiness_probe_reports_database_ready() -> None:
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_storage_factory_preserves_local_default_and_selects_s3(monkeypatch, tmp_path) -> None:
    local = get_storage_backend("local", root=tmp_path)
    assert isinstance(local, LocalFileStorage)

    calls = []
    monkeypatch.setattr(boto3, "client", lambda service, **kwargs: calls.append((service, kwargs)) or object())
    s3 = get_storage_backend("s3", bucket="phase14-bucket", region="ap-southeast-1")
    assert isinstance(s3, ObjectStorage)
    assert calls[0][0] == "s3"
    assert calls[0][1]["region_name"] == "ap-southeast-1"
    assert calls[0][1]["aws_access_key_id"] if settings.s3_access_key_id and settings.s3_secret_access_key else True


def test_s3_storage_has_writable_export_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(boto3, "client", lambda service, **kwargs: object())
    storage = get_storage_backend("s3", bucket="phase14-bucket")
    path = storage.writable_path("package.zip")
    path.write_bytes(b"zip data")
    assert path.read_bytes() == b"zip data"


def test_ai_provider_defaults_to_ollama_and_unknown_provider_degrades(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_provider", "ollama")
    assert isinstance(get_llm_provider(), OllamaProvider)
    provider = get_llm_provider("future-hosted")
    assert provider.generate_grounded_json({}) is None


def test_production_configuration_rejects_missing_auth_and_hosts() -> None:
    env = os.environ.copy()
    env.update({"APP_ENVIRONMENT": "production", "AUTH_REQUIRED": "false", "DATABASE_URL": "sqlite:///./data/phase14-config-test.db"})
    result = subprocess.run([sys.executable, "-c", "import backend.app.config"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "AUTH_REQUIRED must be true in production" in result.stderr
