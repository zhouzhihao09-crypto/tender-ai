"""Phase 15B-1: Durable Background Jobs + Processing Reliability.

Verifies the RQ/Redis durable-job architecture and its graceful fallback
to process-local execution when Redis is unavailable (local development
and test environments).
"""

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.jobs import run_analysis
from backend.app.main import app
from backend.app.queue import (
    enqueue_analysis,
    is_queue_available,
    reset_queue_cache,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_config_has_redis_url() -> None:
    assert hasattr(settings, "redis_url")
    assert settings.redis_url == ""  # unset in tests


def test_config_has_job_retry_settings() -> None:
    assert settings.job_max_retries == 3
    assert settings.job_timeout == "30m"


# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------


def test_jobs_module_imports() -> None:
    assert callable(run_analysis)


def test_queue_module_imports() -> None:
    assert callable(enqueue_analysis)
    assert callable(is_queue_available)
    assert callable(reset_queue_cache)


def test_worker_module_importable() -> None:
    from backend.app import worker

    assert worker.main is not None
    assert worker.QUEUE_NAME == "tender-ai"


# ---------------------------------------------------------------------------
# Fallback behaviour (no Redis)
# ---------------------------------------------------------------------------


def test_is_queue_available_false_without_redis() -> None:
    reset_queue_cache()
    assert is_queue_available() is False


def test_enqueue_analysis_uses_background_tasks_fallback() -> None:
    """When Redis is not configured, enqueue_analysis delegates to the
    provided BackgroundTasks object rather than trying RQ."""
    reset_queue_cache()
    bt = BackgroundTasks()
    job_id = enqueue_analysis(99999, background_tasks=bt)
    assert job_id is None  # not an RQ job
    assert len(bt.tasks) == 1  # noqa: SLF001 -- public attribute on installed Starlette


def test_enqueue_analysis_runs_sync_without_background_tasks() -> None:
    """When Redis is not configured and no BackgroundTasks is provided,
    enqueue_analysis runs the job synchronously."""
    reset_queue_cache()

    captured: list[int] = []
    import backend.app.jobs as jobs_mod
    import backend.app.queue as queue_mod

    def fake_run(tender_id: int) -> None:
        captured.append(tender_id)

    original = jobs_mod.run_analysis
    jobs_mod.run_analysis = fake_run  # type: ignore[assignment]
    queue_mod.run_analysis = fake_run
    try:
        result = enqueue_analysis(42)
        assert result is None
        assert captured == [42]
    finally:
        jobs_mod.run_analysis = original  # type: ignore[assignment]
        queue_mod.run_analysis = original


def test_enqueue_analysis_does_not_import_rq_without_redis() -> None:
    """When REDIS_URL is empty, the rq module should never be imported."""
    reset_queue_cache()
    import sys

    sys.modules.pop("rq", None)
    enqueue_analysis(99999)
    assert "rq" not in sys.modules


# ---------------------------------------------------------------------------
# End-to-end fallback (BackgroundTasks via HTTP)
# ---------------------------------------------------------------------------


def test_create_tender_dispatches_background_fallback() -> None:
    """Posting a minimal PDF returns 201 + QUEUED; analysis runs via
    BackgroundTasks fallback when Redis is unavailable."""
    from tests.test_pdf_service import make_text_pdf

    response = client.post(
        "/api/tenders",
        files={"file": ("test.pdf", make_text_pdf(["Reference: TEST-001"]), "application/pdf")},
    )
    assert response.status_code == 201
    tender = response.json()
    assert tender["analysis_status"] == "QUEUED"
    # BackgroundTasks run synchronously in TestClient, so analysis should
    # complete (or fail gracefully) for a minimal PDF.
    insight = client.get(f"/api/tenders/{tender['id']}/insight")
    assert insight.status_code == 200
    status = insight.json()["tender"]["analysis_status"]
    assert status in {"QUEUED", "PROCESSING", "COMPLETED", "FAILED"}


def test_retry_endpoint_falls_back_to_background_tasks() -> None:
    """The /retry endpoint works via the BackgroundTasks fallback."""
    from tests.test_pdf_service import make_text_pdf

    response = client.post(
        "/api/tenders",
        files={"file": ("retry-test.pdf", make_text_pdf(["Reference: RETRY-001"]), "application/pdf")},
    )
    tender_id = response.json()["id"]
    for _ in range(20):
        status = client.get(f"/api/tenders/{tender_id}/insight").json()["tender"]["analysis_status"]
        if status == "COMPLETED":
            break

    retry = client.post(f"/api/tenders/{tender_id}/retry")
    assert retry.status_code == 200
    assert retry.json()["analysis_status"] == "QUEUED"


def test_reanalyze_endpoint_falls_back_to_background_tasks() -> None:
    """The /reanalyze endpoint works via the BackgroundTasks fallback."""
    from tests.test_pdf_service import make_text_pdf

    response = client.post(
        "/api/tenders",
        files={"file": ("reanalyze-test.pdf", make_text_pdf(["Reference: REAL-001"]), "application/pdf")},
    )
    tender_id = response.json()["id"]
    for _ in range(20):
        status = client.get(f"/api/tenders/{tender_id}/insight").json()["tender"]["analysis_status"]
        if status == "COMPLETED":
            break

    reanalyze = client.post(f"/api/tenders/{tender_id}/reanalyze")
    assert reanalyze.status_code == 200
    assert reanalyze.json()["analysis_status"] == "QUEUED"


# ---------------------------------------------------------------------------
# No duplicate processing
# ---------------------------------------------------------------------------


def test_reanalyze_rejected_when_already_processing(monkeypatch) -> None:
    """The reanalyze endpoint returns 409 when analysis is in progress.

    The background job is stubbed so the tender stays in QUEUED state;
    this deterministically exercises the "already in progress" guard in
    ``api.reanalyze_tender`` without relying on timing of the real
    analysis worker.
    """
    from tests.test_pdf_service import make_text_pdf

    monkeypatch.setattr("backend.app.jobs.run_analysis", lambda tender_id: None)
    response = client.post(
        "/api/tenders",
        files={"file": ("dup-test.pdf", make_text_pdf(["Reference: DUP-001"]), "application/pdf")},
    )
    tender_id = response.json()["id"]
    duplicate = client.post(f"/api/tenders/{tender_id}/reanalyze")
    assert duplicate.status_code == 409


def test_run_analysis_handles_nonexistent_tender() -> None:
    """run_analysis should not crash for a non-existent tender."""
    run_analysis(999999)

