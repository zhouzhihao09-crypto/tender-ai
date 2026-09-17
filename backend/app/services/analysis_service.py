import json
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..analysis_schemas import TenderAnalysis
from ..config import settings
from ..models import AnalysisStatus, Tender, TenderChecklistItem, TenderDate, TenderDocument, TenderQuestion, TenderRequirement, TenderRisk, TenderSource, TenderStatus
from ..storage import document_storage
from .pdf_service import chunk_pages, extract_pdf_pages
from .retrieval_service import retrieve
from .embedding_service import build_tender_index
from .llm_service import generate_grounded_json


def _snippet(text: str, term: str) -> str:
    match = re.search(term, text, re.IGNORECASE)
    if not match:
        return text[:280]
    return text[max(0, match.start() - 80):match.end() + 200].strip()


def _first_matching(chunks: list[dict[str, object]], terms: str) -> tuple[int | None, str | None]:
    for chunk in chunks:
        if re.search(terms, str(chunk["text"]), re.IGNORECASE):
            return int(chunk["page"]), str(chunk["text"])
    return None, None


def build_local_analysis(tender: Tender, chunks: list[dict[str, object]]) -> TenderAnalysis:
    full_text = "\n".join(str(chunk["text"]) for chunk in chunks)
    title = tender.title
    reference_match = re.search(r"(?:tender\s+reference\s+(?:no\.?|number)|reference\s+(?:no\.?|number)|tender\s*no|reference)\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{2,})", full_text, re.IGNORECASE)
    organisation_match = re.search(r"(?:issued by|issuing organisation|organisation|organization)\s*[:\-]\s*([^\n.]{3,100})", full_text, re.IGNORECASE)
    scope_match = re.search(r"(?:works include but are not limited to|scope of (?:works|services)|scope)\s*[:\-]?\s*([^.]{3,300})", full_text, re.IGNORECASE)
    location_match = re.search(r"(?:project location|location|site)\s*[:\-]\s*([^\n.]{3,120})", full_text, re.IGNORECASE)
    summary = {"title": title, "reference": reference_match.group(1).strip(".,;: ") if reference_match else None, "organisation": organisation_match.group(1).strip() if organisation_match else None, "scope": scope_match.group(1).strip() if scope_match else None, "location": location_match.group(1).strip() if location_match else None}
    dates = []
    date_pattern = re.compile(r"(?P<event>submission deadline|closing date|tender closes|clarification deadline|site visit|tender issue date|contract start|completion date)[^\n]{0,100}?(?P<date>\d{1,2}[ ./-](?:\d{1,2}|[A-Za-z]{3,9})[ ./-]\d{2,4}|\d{4}[ ./-]\d{1,2}[ ./-]\d{1,2})(?:\s*(?:at|@|,)?\s*(?P<time>\d{1,2}:\d{2}\s*(?:AM|PM)?|\d{1,2}\s*(?:AM|PM)))?", re.IGNORECASE)
    for match in date_pattern.finditer(full_text):
        page, source = _first_matching(chunks, re.escape(match.group(0)[:45]))
        dates.append({"event": match.group("event").title(), "date": match.group("date"), "time": match.group("time"), "evidence_type": "EXPLICIT", "source_page": page, "source_snippet": _snippet(source or match.group(0), match.group("date"))})
    clarification_match = re.search(r"clarifications?\s+must\s+be\s+submitted[^\n]*?no\s+later\s+than\s+(\d{1,2}[ ./-](?:\d{1,2}|[A-Za-z]{3,9})[ ./-]\d{2,4})", full_text, re.IGNORECASE)
    if clarification_match:
        page, source = _first_matching(chunks, "clarifications?\\s+must\\s+be\\s+submitted")
        dates.append({"event": "Clarification Deadline", "date": clarification_match.group(1), "time": None, "evidence_type": "EXPLICIT", "source_page": page, "source_snippet": _snippet(source or clarification_match.group(0), clarification_match.group(1))})
    validity_match = re.search(r"(?:tender|bid|offer)\s+valid(?:ity| for)\s*(?:period(?: of)?\s*)?[:\-]?\s*(\d+\s+(?:calendar\s+)?days?)", full_text, re.IGNORECASE)
    if validity_match:
        page, source = _first_matching(chunks, "valid(?:ity| for)")
        dates.append({"event": "Validity Period", "date": validity_match.group(1), "time": None, "evidence_type": "EXPLICIT", "source_page": page, "source_snippet": _snippet(source or validity_match.group(0), validity_match.group(1))})
    requirements = []
    submission_page, submission_source = _first_matching(chunks, r"tender\s+submission\s+requirements")
    submission_requirements = [(r"completed\s+BOQ[^\n]*SGD", "Completed BOQ with total cost in SGD", "Commercial"), (r"detailed\s+work\s+programme[^\n]*timeline", "Detailed work programme with timeline", "Technical"), (r"list\s+of\s+proposed\s+subcontractors[^\n]*qualifications", "Proposed subcontractor list with qualifications", "Administrative"), (r"company\s+profile[^\n]*BCA\s+registration[^\n]*licen[cs]es", "Company profile with BCA registration and relevant licences", "Eligibility"), (r"evidence\s+of\s+similar\s+projects[^\n]*past\s+5\s+years", "Evidence of similar projects completed in the past 5 years", "Eligibility"), (r"safety\s+and\s+risk\s+assessment\s+plan", "Safety and Risk Assessment Plan", "Safety")]
    if submission_source:
        for pattern, title_text, category in submission_requirements:
            if re.search(pattern, submission_source, re.IGNORECASE):
                requirements.append({"title": title_text, "description": _snippet(submission_source, pattern), "category": category, "mandatory": True, "status": "NOT_STARTED", "evidence_type": "EXPLICIT", "source_page": submission_page, "source_snippet": _snippet(submission_source, pattern)})
    else:
        fallback_requirements = [(r"completed BOQ|bill of quantities", "Completed BOQ", "Commercial"), (r"company profile", "Company profile", "Administrative"), (r"similar project|relevant experience", "Evidence of relevant experience", "Eligibility"), (r"insurance", "Insurance documentation", "Insurance"), (r"safety|risk assessment", "Safety and risk assessment plan", "Safety"), (r"licen[cs]e|registration", "Required licences or registrations", "Eligibility")]
        for pattern, title_text, category in fallback_requirements:
            page, source = _first_matching(chunks, pattern)
            if source:
                requirements.append({"title": title_text, "description": _snippet(source, pattern), "category": category, "mandatory": bool(re.search(r"must|required|mandatory", source, re.IGNORECASE)), "status": "NOT_STARTED", "evidence_type": "EXPLICIT", "source_page": page, "source_snippet": _snippet(source, pattern)})
    insurance_page, insurance_source = _first_matching(chunks, r"contractor\s+to\s+maintain\s+adequate\s+public\s+liability")
    if insurance_source:
        requirements.append({"title": "Required contractor insurance", "description": _snippet(insurance_source, r"contractor\s+to\s+maintain\s+adequate\s+public\s+liability"), "category": "Insurance", "mandatory": True, "status": "NOT_STARTED", "evidence_type": "EXPLICIT", "source_page": insurance_page, "source_snippet": _snippet(insurance_source, r"contractor\s+to\s+maintain\s+adequate\s+public\s+liability")})
    documents = [{"name": item["title"], "required": item["mandatory"], "status": "NOT_STARTED", "evidence_type": item["evidence_type"], "source_page": item["source_page"], "source_snippet": item["source_snippet"]} for item in requirements]
    risks = []
    for pattern, title_text, severity, action in [(r"submission deadline|closing date", "Submission deadline", "HIGH", "Complete a compliance review before the stated closing time."), (r"insurance", "Insurance documentation", "MEDIUM", "Confirm coverage levels and obtain the current certificates."), (r"site visit", "Site visit obligation", "MEDIUM", "Confirm whether attendance is mandatory and record the visit details.")]:
        page, source = _first_matching(chunks, pattern)
        if source:
            risks.append({"title": title_text, "severity": severity, "explanation": _snippet(source, pattern), "recommended_action": action, "evidence_type": "INFERENCE", "source_page": page, "source_snippet": _snippet(source, pattern)})
    page, source = _first_matching(chunks, r"automatic(?:ally)?\s+disqualif|penalt|liquidated damages|failure to")
    if source:
        risks.append({"title": "Stated consequence or penalty", "severity": "HIGH", "explanation": _snippet(source, r"automatic(?:ally)?\s+disqualif|penalt|liquidated damages|failure to"), "recommended_action": "Review the stated consequence with the bid owner; this item is explicitly stated in the tender.", "evidence_type": "EXPLICIT", "source_page": page, "source_snippet": _snippet(source, r"automatic(?:ally)?\s+disqualif|penalt|liquidated damages|failure to")})
    eligibility = [item for item in requirements if item["category"] == "Eligibility"]
    clarifications = []
    if any(item["title"] == "Insurance documentation" for item in requirements) and not re.search(r"\$|sgd|coverage|limit", full_text, re.IGNORECASE):
        page, source = _first_matching(chunks, "insurance")
        clarifications.append({"question": "What insurance coverage amount is required?", "why_it_matters": "The tender requests insurance documentation but the available text does not state a coverage amount.", "source_page": page, "source_snippet": _snippet(source or "", "insurance")})
    reasons = [f"Tender evidence identifies a {item['category'].lower()} requirement: {item['title']}." for item in requirements if item["mandatory"]]
    verify = [f"Confirm the company can satisfy: {item['title']}." for item in eligibility]
    return TenderAnalysis.model_validate({"summary": summary, "dates": dates, "requirements": requirements, "documents": documents, "eligibility": eligibility, "risks": risks, "clarifications": clarifications, "bid_assessment": {"recommendation": "REVIEW_REQUIRED", "confidence": "LOW", "rationale": "The tender contains requirements that need review, but Tender AI does not yet have the company's qualification profile.", "reasons": reasons, "verify": verify, "concerns": ["Company qualifications have not been provided."]}})


def _llm_result_is_grounded(result: TenderAnalysis, chunks: list[dict[str, object]]) -> bool:
    pages = {int(chunk["page"]): re.sub(r"\s+", " ", str(chunk["text"])).lower() for chunk in chunks}
    evidence_items = [*result.dates, *result.requirements, *result.documents, *result.eligibility, *result.risks, *result.clarifications]
    for item in evidence_items:
        if item.source_page is None or not item.source_snippet or item.source_page not in pages:
            return False
        snippet = re.sub(r"\s+", " ", item.source_snippet).lower().strip()
        if snippet and snippet not in pages[item.source_page]:
            return False
    return True


def persist_analysis(db: Session, tender: Tender, analysis: TenderAnalysis, chunks: list[dict[str, object]]) -> None:
    previous_checklist = {item.title: item.status for item in db.scalars(select(TenderChecklistItem).where(TenderChecklistItem.tender_id == tender.id))}
    for model in (TenderRequirement, TenderDocument, TenderDate, TenderRisk, TenderQuestion, TenderSource, TenderChecklistItem):
        db.execute(delete(model).where(model.tender_id == tender.id))
    for chunk in chunks:
        db.add(TenderSource(tender_id=tender.id, page=int(chunk["page"]), snippet=str(chunk["text"]), chunk_index=int(chunk["chunk_index"]), chunk_id=str(chunk["chunk_id"]), retrieval_method="indexed"))
    for item in analysis.requirements:
        db.add(TenderRequirement(tender_id=tender.id, requirement=item.title, category=item.category, mandatory=item.mandatory, status=item.status, evidence_type=item.evidence_type, source_page=item.source_page, source_snippet=item.source_snippet))
    for item in analysis.documents:
        db.add(TenderDocument(tender_id=tender.id, name=item.name, required=item.required, status=item.status, source_page=item.source_page, source_snippet=item.source_snippet, evidence_type=item.evidence_type))
    for item in analysis.dates:
        db.add(TenderDate(tender_id=tender.id, event=item.event, date_value=item.date, time_value=item.time, source_page=item.source_page, source_snippet=item.source_snippet, evidence_type=item.evidence_type))
    for item in analysis.risks:
        db.add(TenderRisk(tender_id=tender.id, title=item.title, severity=item.severity, explanation=item.explanation, recommended_action=item.recommended_action, source_page=item.source_page, source_snippet=item.source_snippet, evidence_type=item.evidence_type))
    for item in analysis.clarifications:
        db.add(TenderQuestion(tender_id=tender.id, question=item.question, why_it_matters=item.why_it_matters, source_page=item.source_page, source_snippet=item.source_snippet))
    checklist_titles: set[str] = set()
    for item in analysis.documents:
        if item.name not in checklist_titles:
            title = f"Prepare {item.name}"
            db.add(TenderChecklistItem(tender_id=tender.id, title=title, status=previous_checklist.get(title, "NOT_STARTED"), source_page=item.source_page, source_snippet=item.source_snippet, evidence_type=item.evidence_type))
            checklist_titles.add(item.name)
    for item in analysis.requirements:
        if item.title not in checklist_titles and item.mandatory:
            title = f"Confirm {item.title}"
            db.add(TenderChecklistItem(tender_id=tender.id, title=title, status=previous_checklist.get(title, "NOT_STARTED"), source_page=item.source_page, source_snippet=item.source_snippet, evidence_type=item.evidence_type))
            checklist_titles.add(item.title)
    for item in analysis.clarifications:
        title = f"Resolve clarification: {item.question}"
        db.add(TenderChecklistItem(tender_id=tender.id, title=title, status=previous_checklist.get(title, "NOT_STARTED"), source_page=item.source_page, source_snippet=item.source_snippet, evidence_type="INFERENCE"))
    tender.analysis_json = analysis.model_dump_json()
    tender.summary_json = analysis.summary.model_dump_json()
    tender.analysis_status = AnalysisStatus.COMPLETED
    tender.analysis_completed_at = datetime.utcnow()
    tender.analysis_error = None
    tender.status = TenderStatus.REVIEWING
    tender.progress = 100
    tender.reference = analysis.summary.reference
    tender.organization = analysis.summary.organisation
    for item in analysis.dates:
        if "submission" in item.event.lower() or "closing" in item.event.lower():
            for date_format in ("%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%Y-%m-%d"):
                try:
                    tender.deadline = datetime.strptime(item.date, date_format)
                    break
                except ValueError:
                    continue
    if analysis.risks:
        tender.risk = max((item.severity for item in analysis.risks), key=lambda value: {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(value, 0)).title()
    db.commit()


def analyze_tender(tender_id: int, db: Session) -> None:
    tender = db.get(Tender, tender_id)
    if not tender:
        return
    tender.analysis_status = AnalysisStatus.PROCESSING
    tender.analysis_started_at = datetime.utcnow()
    tender.progress = 10
    db.commit()
    try:
        with document_storage.materialize(tender.stored_file_name) as source_path:
            pages = extract_pdf_pages(source_path)
            chunks = chunk_pages(pages)
        build_tender_index(tender.id, chunks)
        tender.progress = 55
        db.commit()
        analysis = build_local_analysis(tender, chunks)
        evidence = {category: [{"page": item.page, "text": item.text} for item in retrieve(chunks, query)] for category, query in {"requirements": "mandatory required eligibility submission documents", "dates": "deadline closing date site visit contract start", "risks": "penalty insurance obligation submission condition", "clarifications": "unclear missing amount clarification"}.items()}
        llm_result = generate_grounded_json(evidence)
        if llm_result:
            try:
                candidate = TenderAnalysis.model_validate(llm_result)
                if _llm_result_is_grounded(candidate, chunks):
                    analysis = candidate
            except Exception:
                pass
        persist_analysis(db, tender, analysis, chunks)
    except Exception as error:
        tender.analysis_status = AnalysisStatus.FAILED
        tender.analysis_error = str(error) if isinstance(error, ValueError) else "The tender could not be analysed."
        db.commit()


def answer_question(question: str, sources: list[TenderSource]) -> tuple[str, list[TenderSource]]:
    aliases = {"投标截止": "tender closing date time", "提交哪些文件": "tender submission requirements documents", "保险要求": "contractor insurance public liability", "项目范围": "scope of works", "安全风险评估": "safety risk assessment plan", "类似项目": "similar projects past 5 years"}
    normalized_question = question
    for phrase, replacement in aliases.items():
        if phrase in question:
            normalized_question = replacement
            break
    preferred_patterns = []
    lower_question = normalized_question.lower()
    evaluation_intent = bool(re.search(r"evaluation\s+(?:weightings?|criteria|percentages?|methodology)|how\s+(?:will\s+)?(?:the\s+)?tender\s+be\s+evaluat|(?:percentage|percent)\s+(?:is\s+)?assigned|scoring\s+methodology|tender\s+evaluation\s+percentages?", lower_question))
    if evaluation_intent:
        evaluation_terms = re.compile(r"evaluation|evaluat\w*|scor\w*|weight\w*|percent\w*|criteria|criterion|methodology", re.IGNORECASE)
        evaluation_sources = [source for source in sources if len(set(evaluation_terms.findall(source.snippet.lower()))) >= 2]
        if not evaluation_sources:
            return "Evaluation weightings and scoring criteria are not stated in the tender document. I could not find evidence in the provided tender document specifying percentage weightings or scoring criteria for evaluation.", []
        evidence = " ".join(_snippet(source.snippet, evaluation_terms.pattern)[:500] for source in evaluation_sources[:2])
        return f"Based on the tender document: {evidence}", evaluation_sources[:2]
    submission_intent = "submission requirement" in lower_question or ("document" in lower_question and "submit" in lower_question)
    if submission_intent or any(term in lower_question for term in ("document", "file", "submit", "safety", "similar project")):
        preferred_patterns.append(r"tender\s+submission\s+requirements")
    if "insurance" in lower_question:
        preferred_patterns.append(r"contractor\s+to\s+maintain\s+adequate")
    if "clarification" in lower_question:
        preferred_patterns.append(r"clarifications?\s+must\s+be\s+submitted")
    elif any(term in lower_question for term in ("closing", "deadline", "截止")):
        preferred_patterns.append(r"tender\s+closing\s+date")
    if any(term in lower_question for term in ("scope", "范围")):
        preferred_patterns.append(r"works\s+include|scope\s+of\s+works")
    preferred = [source for source in sources if any(re.search(pattern, source.snippet, re.IGNORECASE) for pattern in preferred_patterns)]
    if submission_intent:
        submission_section = [source for source in preferred if re.search(r"bidders\s+must\s+submit\s+the\s+following", source.snippet, re.IGNORECASE)]
        if submission_section:
            preferred = submission_section
    chunks = [{"chunk_id": source.chunk_id, "page": source.page, "text": source.snippet} for source in (preferred or sources)]
    matches = retrieve(chunks, normalized_question)
    if not matches and preferred:
        from .retrieval_service import RetrievedChunk
        matches = [RetrievedChunk(source.chunk_id, source.page, source.snippet, 1.0, "section") for source in preferred[:2]]
    if not matches:
        return "I couldn't find this information in the tender document.", []
    selected = [next(source for source in sources if source.chunk_id == match.chunk_id) for match in matches]
    excerpt_pattern = preferred_patterns[0] if preferred_patterns else re.escape(normalized_question.split()[0])
    if submission_intent:
        evidence_parts = []
        for match in matches[:1]:
            section_match = re.search(r"5\.\s*Tender Submission Requirements.*?(?=\n\s*6\.\s*Contractual Framework|$)", match.text, re.IGNORECASE | re.DOTALL)
            evidence_parts.append((section_match.group(0) if section_match else match.text).strip())
        evidence = " ".join(evidence_parts)
    else:
        evidence = " ".join(_snippet(match.text, excerpt_pattern)[:500] for match in matches[:2])
    return f"Based on the tender document: {evidence}", selected