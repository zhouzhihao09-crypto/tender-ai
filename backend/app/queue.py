"""Durable job queue abstraction.

When ``REDIS_URL`` is configured and Redis is reachable, analysis jobs
are enqueued onto an RQ queue and processed by a separate worker process
(:mod:`backend.app.worker`).  This provides:

* **Durability** — jobs are stored in Redis and survive application restarts.
* **Process isolation** — the uvicorn worker never blocks on PDF analysis.
* **Retries** — RQ automatically retries failed jobs with exponential backoff.
* **Visibility** — ``rq info`` shows queue depth and worker status.
* **No duplicate processing** — jobs use a unique ID per tender
  (``analysis:<tender_id>``) and the API endpoints also guard against
  re-enqueuing an already-queued or in-progress tender.

When Redis is **not** configured or unreachable, the system falls back to
FastAPI ``BackgroundTasks`` (local development and test environments),
preserving the existing synchronous-in-TestClient behaviour.
"""

import logging

from .config import settings

logger = logging.getLogger(__name__)

#: Name of the RQ queue and worker queue tag.
QUEUE_NAME = "tender-ai"

# ---------------------------------------------------------------------------
# Cached availability state — avoids a Redis round-trip on every enqueue.
# Tests can reset via reset_queue_cache().
# ---------------------------------------------------------------------------
_redis_checked: bool = False
_redis_available: bool = False
_rq_queue = None  # type: ignore[assignment]


def reset_queue_cache() -> None:
    """Reset cached Redis/RQ state.  Intended for tests."""
    global _redis_checked, _redis_available, _rq_queue
    _redis_checked = False
    _redis_available = False
    _rq_queue = None


def _check_redis() -> bool:
    """Probe Redis connectivity.  Cached after the first check."""
    global _redis_checked, _redis_available
    if _redis_checked:
        return _redis_available
    if not settings.redis_url:
        _redis_available = False
        _redis_checked = True
        return False
    try:
        import redis as _redis

        client = _redis.from_url(
            settings.redis_url,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        client.ping()
        _redis_available = True
    except Exception:
        _redis_available = False
        logger.warning(
            "Redis not reachable at %s; falling back to BackgroundTasks.",
            settings.redis_url,
        )
    finally:
        _redis_checked = True
    return _redis_available


def is_queue_available() -> bool:
    """Return ``True`` if the RQ/Redis backend is available for durable dispatch."""
    return _check_redis()


def _get_rq_queue():
    """Return the cached RQ ``Queue``, creating it on first call."""
    global _rq_queue
    if _rq_queue is None:
        import redis as _redis
        import rq as _rq

        conn = _redis.from_url(settings.redis_url)
        _rq_queue = _rq.Queue(QUEUE_NAME, connection=conn)
    return _rq_queue


def enqueue_analysis(tender_id: int, background_tasks=None) -> str | None:
    """Enqueue a tender-analysis job for durable processing.

    * **Redis available** → the job is enqueued on RQ with retry support
      and a unique job ID (``analysis:<tender_id>``) that prevents
      duplicate dispatch for the same tender.
    * **Redis not available, ``background_tasks`` provided** → the job is
      dispatched via FastAPI ``BackgroundTasks`` (local-dev / test fallback).
    * **Redis not available, no ``background_tasks``** → the job runs
      synchronously in the calling thread (programmatic use).

    Returns the RQ job ID when dispatched via RQ, otherwise ``None``.
    """
    from .jobs import run_analysis

    # Fast path: no Redis configured at all → always use fallback.
    if not settings.redis_url:
        _fallback_dispatch(tender_id, background_tasks)
        return None

    # Redis URL is configured → try RQ, fall back on any error.
    if _check_redis():
        try:
            queue = _get_rq_queue()
            from rq import Retry

            job = queue.enqueue_call(
                "backend.app.jobs.run_analysis",
                args=(tender_id,),
                job_id=f"analysis:{tender_id}",
                unique=True,
                retry=Retry(max=settings.job_max_retries),
                timeout=settings.job_timeout,
            )
            logger.info(
                "Enqueued analysis job %s for tender %d via RQ",
                job.id,
                tender_id,
            )
            return job.id
        except Exception:
            logger.warning(
                "Failed to enqueue RQ job for tender %d; falling back.",
                tender_id,
            )

    # Fallback (Redis configured but unreachable, or RQ enqueue failed)
    _fallback_dispatch(tender_id, background_tasks)
    return None


def _fallback_dispatch(tender_id: int, background_tasks=None) -> None:
    """Dispatch via ``BackgroundTasks`` or run synchronously."""
    from .jobs import run_analysis

    if background_tasks is not None:
        background_tasks.add_task(run_analysis, tender_id)
        logger.info(
            "Tender %d: queued analysis via BackgroundTasks fallback (no Redis)",
            tender_id,
        )
    else:
        logger.info(
            "Tender %d: running analysis synchronously (no Redis, no BackgroundTasks)",
            tender_id,
        )
        run_analysis(tender_id)
