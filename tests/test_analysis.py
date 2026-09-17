from backend.app.analysis_schemas import TenderAnalysis
from backend.app.main import app
from backend.app.models import Tender
from backend.app.services.analysis_service import _llm_result_is_grounded, build_local_analysis, persist_analysis
from backend.app.services.retrieval_service import retrieve


def test_retrieval_prefers_exact_tender_terms() -> None:
    chunks = [
        {"chunk_id": "p1-c0", "page": 1, "text": "The submission deadline is 30 September 2026 at 5:00 PM."},
        {"chunk_id": "p4-c1", "page": 4, "text": "The contractor must provide insurance and a completed BOQ."},
    ]
    results = retrieve(chunks, "What is the submission deadline?")
    assert results[0].page == 1
    assert results[0].method in {"keyword", "hybrid"}


def test_local_analysis_extracts_organisation_and_location_from_labelled_text() -> None:
    """Clearly labelled ``Issued by:`` and ``Location:`` lines populate the
    summary organisation and location fields."""
    tender = Tender(title="SCA tender", file_name="sca.pdf", stored_file_name="safe.pdf")
    chunks = [
        {"chunk_id": "p1-c0", "page": 1, "chunk_index": 0, "text": "Location: 600 West Coast Road, Singapore 127445\n\nIssued by: Singapore Cricket Association\n\nTender reference: SCA/MAINCON/2025/01"},
    ]
    analysis = build_local_analysis(tender, chunks)
    assert analysis.summary.organisation == "Singapore Cricket Association"
    assert analysis.summary.location == "600 West Coast Road, Singapore 127445"


def test_local_analysis_returns_none_when_organisation_and_location_are_absent() -> None:
    """When no labelled organisation/location text exists the parser must
    return None rather than guessing from nearby prose."""
    tender = Tender(title="Generic Works", file_name="generic.pdf", stored_file_name="safe.pdf")
    chunks = [
        {"chunk_id": "p1-c0", "page": 1, "chunk_index": 0, "text": "The contractor will build an office extension. Closing date: 30 September 2026 at 5:00 PM."},
    ]
    analysis = build_local_analysis(tender, chunks)
    assert analysis.summary.organisation is None
    assert analysis.summary.location is None


def test_persist_analysis_stores_extracted_organisation_on_tender() -> None:
    """The extracted organisation must be persisted onto the Tender row so
    it survives re-reads through the API."""
    from backend.app.database import SessionLocal

    tender = Tender(title="SCA tender", file_name="sca.pdf", stored_file_name="safe.pdf")
    chunks = [
        {"chunk_id": "p1-c0", "page": 1, "chunk_index": 0, "text": "Location: 600 West Coast Road, Singapore 127445\n\nIssued by: Singapore Cricket Association\n\nTender reference: SCA/MAINCON/2025/01"},
    ]
    analysis = build_local_analysis(tender, chunks)
    with SessionLocal() as db:
        db.add(tender)
        db.commit()
        db.refresh(tender)
        persist_analysis(db, tender, analysis, chunks)
        assert tender.organization == "Singapore Cricket Association"
        assert tender.reference == "SCA/MAINCON/2025/01"


def test_local_analysis_is_conservative_and_source_aware() -> None:
    tender = Tender(title="Office Renovation", file_name="office.pdf", stored_file_name="safe.pdf")
    chunks = [
        {"chunk_id": "p1-c0", "page": 1, "chunk_index": 0, "text": "Reference: OR-2026. Issued by: City Council. Submission deadline: 30 September 2026 at 5:00 PM."},
        {"chunk_id": "p3-c1", "page": 3, "chunk_index": 1, "text": "The bidder must submit a completed BOQ, company profile and proof of similar project experience. Insurance documentation is required."},
    ]
    analysis = build_local_analysis(tender, chunks)
    assert analysis.summary.reference == "OR-2026"
    assert analysis.dates[0].source_page == 1
    assert any(item.title == "Completed BOQ" for item in analysis.requirements)
    assert analysis.bid_assessment.recommendation == "REVIEW_REQUIRED"
    assert analysis.bid_assessment.confidence == "LOW"


def test_analysis_extracts_time_validity_scope_and_explicit_consequence() -> None:
    tender = Tender(title="Facilities Tender", file_name="facilities.pdf", stored_file_name="safe.pdf")
    chunks = [{"chunk_id": "p2-c0", "page": 2, "chunk_index": 0, "text": "Scope: Facilities maintenance at the civic centre. Closing date: 30 September 2026 at 5:30 PM. Tender validity period: 90 days. Failure to submit the signed form will automatically disqualify the bid."}]
    analysis = build_local_analysis(tender, chunks)
    assert analysis.summary.scope == "Facilities maintenance at the civic centre"
    assert any(item.time == "5:30 PM" for item in analysis.dates)
    assert any(item.event == "Validity Period" and item.date == "90 days" for item in analysis.dates)
    consequence = next(item for item in analysis.risks if item.evidence_type == "EXPLICIT")
    assert consequence.source_page == 2
    assert "automatically disqualify" in (consequence.source_snippet or "")


def test_llm_claim_with_fabricated_source_is_rejected() -> None:
    candidate = TenderAnalysis.model_validate({"summary": {}, "dates": [{"event": "Closing Date", "date": "1 January 2099", "evidence_type": "EXPLICIT", "source_page": 1, "source_snippet": "This sentence is not in the tender."}], "bid_assessment": {"recommendation": "REVIEW_REQUIRED", "confidence": "LOW", "rationale": "Unknown."}})
    assert not _llm_result_is_grounded(candidate, [{"page": 1, "text": "Closing date: 30 September 2026."}])