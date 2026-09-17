"""Phase 15B-5: Docker non-root hardening — static validation tests.

Docker is not available in this environment, so the Dockerfile is
validated statically rather than by building and running the image.
These tests assert the hardening properties that must hold for the
container to run as a non-root user without losing functionality.
"""

from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
TEXT = DOCKERFILE.read_text(encoding="utf-8")
LINES = TEXT.splitlines()


def test_dockerfile_exists() -> None:
    assert DOCKERFILE.is_file()


def test_dockerfile_uses_python_slim_base_image() -> None:
    assert any(line.startswith("FROM python:3.12-slim") for line in LINES)


def test_dockerfile_creates_a_non_root_user() -> None:
    """A dedicated application user and group must be created."""
    assert any("useradd" in line and "appuser" in line for line in LINES)
    assert any("groupadd" in line and "appuser" in line for line in LINES)


def test_dockerfile_user_is_not_root() -> None:
    """The final USER directive must not be root."""
    user_lines = [line for line in LINES if line.strip().startswith("USER ")]
    assert user_lines, "expected a USER directive"
    assert user_lines[-1].strip() == "USER appuser:appuser"
    assert "root" not in user_lines[-1]


def test_dockerfile_runtime_directives_come_after_user() -> None:
    """USER must precede CMD so the process actually runs as appuser."""
    idx_user = next(i for i, line in enumerate(LINES) if line.strip().startswith("USER "))
    idx_cmd = next(i for i, line in enumerate(LINES) if line.strip().startswith('CMD ['))
    assert idx_user < idx_cmd, "USER must appear before CMD"


def test_dockerfile_runtime_directories_are_created() -> None:
    """All application data directories must exist inside the image."""
    mkdir_line = next(line for line in LINES if line.strip().startswith("RUN mkdir -p"))
    for directory in ("/app/data", "/app/uploads", "/app/company_evidence", "/app/exports"):
        assert directory in mkdir_line


def test_dockerfile_runtime_directories_are_chowned() -> None:
    """The non-root user must own the writable data directories."""
    assert "chown -R appuser:appuser" in TEXT
    for directory in ("/app/data", "/app/uploads", "/app/company_evidence", "/app/exports"):
        assert directory in TEXT


def test_dockerfile_does_not_use_chmod_777() -> None:
    assert "chmod 777" not in TEXT


def test_dockerfile_does_not_enable_privileged_mode() -> None:
    assert "--privileged" not in TEXT


def test_dockerfile_preserves_health_port() -> None:
    assert "EXPOSE 8000" in TEXT


def test_dockerfile_preserves_python_environment() -> None:
    assert "PYTHONDONTWRITEBYTECODE=1" in TEXT
    assert "PYTHONUNBUFFERED=1" in TEXT


def test_dockerfile_preserves_uvicorn_command() -> None:
    cmd_line = next(line for line in LINES if line.strip().startswith('CMD ['))
    assert "uvicorn" in cmd_line
    assert "backend.app.main:app" in cmd_line
    assert "0.0.0.0" in cmd_line
    assert "8000" in cmd_line