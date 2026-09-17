from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    evidence_type: Literal["EXPLICIT", "INFERENCE", "UNKNOWN"] = "UNKNOWN"
    source_page: int | None = None
    source_snippet: str | None = None


class AnalysisSummary(BaseModel):
    title: str | None = None
    reference: str | None = None
    organisation: str | None = None
    scope: str | None = None
    location: str | None = None


class AnalysisDate(Evidence):
    event: str
    date: str
    time: str | None = None


class AnalysisRequirement(Evidence):
    title: str
    description: str
    category: str = "Administrative"
    mandatory: bool = False
    status: str = "NOT_STARTED"


class AnalysisDocument(Evidence):
    name: str
    required: bool = False
    status: str = "NOT_STARTED"


class AnalysisRisk(Evidence):
    title: str
    severity: str = "MEDIUM"
    explanation: str
    recommended_action: str


class AnalysisClarification(Evidence):
    question: str
    why_it_matters: str


class BidAssessment(BaseModel):
    recommendation: Literal["LIKELY_FIT", "REVIEW_REQUIRED", "LIKELY_NOT_FIT", "INSUFFICIENT_INFORMATION"] = "REVIEW_REQUIRED"
    confidence: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    rationale: str
    reasons: list[str] = Field(default_factory=list)
    verify: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


class TenderAnalysis(BaseModel):
    summary: AnalysisSummary
    dates: list[AnalysisDate] = Field(default_factory=list)
    requirements: list[AnalysisRequirement] = Field(default_factory=list)
    documents: list[AnalysisDocument] = Field(default_factory=list)
    eligibility: list[AnalysisRequirement] = Field(default_factory=list)
    risks: list[AnalysisRisk] = Field(default_factory=list)
    clarifications: list[AnalysisClarification] = Field(default_factory=list)
    bid_assessment: BidAssessment


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    page: int
    snippet: str
    chunk_id: str
    relevance_score: float | None
    retrieval_method: str | None


class AskResponse(BaseModel):
    answer: str
    sources: list[SourceRead]