from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class CompanyProfileUpdate(BaseModel):
    company_name: str | None = None
    description: str | None = None
    business_type: str | None = None
    industry: str | None = None
    years_in_business: int | None = None
    main_services: str | None = None
    areas_of_expertise: str | None = None
    relevant_licences: str | None = None
    certifications: str | None = None
    registrations: str | None = None
    bca_registration_info: str | None = None
    past_project_experience: str | None = None
    typical_project_size: str | None = None
    maximum_project_size: str | None = None
    geographic_coverage: str | None = None
    number_of_employees: int | None = None
    key_capabilities: str | None = None
    insurance_coverage: str | None = None
    other_qualifications: str | None = None


class CompanyProfileRead(CompanyProfileUpdate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    updated_at: datetime


class RequirementMatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    match_status: Literal["MATCH", "PARTIAL_MATCH", "NO_MATCH", "UNKNOWN"]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    company_field: str | None
    company_value: str | None
    company_explanation: str
    critical: bool
    evidence_document_count: int
    evidence_summary: str | None


class BidActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    reason: str
    priority: str
    status: str
    source_page: int | None
    source_snippet: str | None
    company_field: str | None


class CompanyAssessmentRead(BaseModel):
    recommendation: str
    confidence: str
    readiness_score: int | None
    explanation: str
    profile_version: int
    matches: list[RequirementMatchRead]
    actions: list[BidActionRead]
    counts: dict[str, int]


class BidActionStatusUpdate(BaseModel):
    status: Literal["NOT_STARTED", "IN_PROGRESS", "DONE", "BLOCKED"]