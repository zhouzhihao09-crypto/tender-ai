"""RQ worker entry point for durable background job processing.

Usage::

    python -m backend.app.worker          # from project root
    rq worker tender-ai                    # alternative

The worker listens on the ``tender-ai`` queue and processes jobs enqueued
by the web application via :mod:`backend.app.queue`.

Requires ``REDIS_URL`` to be set in the environment.  Start one or more
workers behind a process manager (systemd, Docker, Kubernetes, etc.)
for high availability.
"""

import logging
import sys

from .config import settings

logger = logging.getLogger(__name__)

QUEUE_NAME = "tender-ai"


def main() -> int:
    """Run the RQ worker until interrupted."""
    if not settings.redis_url:
        print(
            "REDIS_URL is not configured. The RQ worker requires Redis.\n"
            "Set REDIS_URL=redis://localhost:6379/0 in your environment.",
            file=sys.stderr,
        )
        return 1

    import redis
    from rq import Worker

    conn = redis.from_url(settings.redis_url)
    worker = Worker(QUEUE_NAME, connection=conn)
    logger.info(
        "Starting RQ worker on queue '%s' (Redis: %s)",
        QUEUE_NAME,
        settings.redis_url,
    )
    worker.work()
    return 0


if __name__ == "__main__":
    sys.exit(main())
