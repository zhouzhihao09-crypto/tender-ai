"""RQ job entry points for durable background processing.

Each function here is a thin adapter that creates its own database session
and delegates to the existing service-layer function. No business logic
lives in this module, keeping the service boundary intact.

These functions are referenced by string path (``backend.app.jobs.run_analysis``)
so that the RQ worker — which may run in a separate process or container —
can import and execute them.
"""

import logging

from sqlalchemy import delete

from .database import SessionLocal
from .models import AnalysisStatus, Tender, UsageEvent

logger = logging.getLogger(__name__)


def run_analysis(tender_id: int) -> None:
    """Run tender analysis as a durable background job.

    Called by the RQ worker (production) or the synchronous /
    ``BackgroundTasks`` fallback (local development and tests).

    Creates its own ``SessionLocal`` so the job is self-contained and
    does not depend on the request-scoped session.
    """
    from .services.analysis_service import analyze_tender

    db = SessionLocal()
    try:
        analyze_tender(tender_id, db)
        tender = db.get(Tender, tender_id)
        if tender and tender.analysis_status == AnalysisStatus.FAILED:
            db.execute(
                delete(UsageEvent).where(
                    UsageEvent.tender_id == tender_id,
                    UsageEvent.usage_type == "TENDER_UPLOAD",
                )
            )
            db.commit()
            logger.warning(
                "Analysis failed for tender %d; rolled back TENDER_UPLOAD usage credits",
                tender_id,
            )
        elif tender and tender.analysis_status == AnalysisStatus.COMPLETED:
            logger.info("Analysis completed for tender %d", tender_id)
    except Exception:
        logger.exception("Unhandled error in analysis job for tender %d", tender_id)
        raise
    finally:
        db.close()
