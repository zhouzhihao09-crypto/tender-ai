from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TenderListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    organization: str | None
    reference: str | None
    deadline: datetime | None
    status: str
    risk: str
    progress: int
    analysis_status: str
    created_at: datetime


class TenderDetail(TenderListItem):
    file_name: str
    analysis_error: str | None


class TenderInsight(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tender: TenderDetail
    analysis: dict | None
    requirements: list[dict]
    documents: list[dict]
    dates: list[dict]
    risks: list[dict]
    clarifications: list[dict]
    sources: list[dict]
    checklist: list[dict]


class ChecklistItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tender_id: int
    title: str
    status: str
    source_page: int | None
    source_snippet: str | None
    evidence_type: str


class ChecklistStatusUpdate(BaseModel):
    status: str


class DashboardTender(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    reference: str | None
    organization: str | None
    deadline: object | None
    analysis_status: str
    risk: str
    status: str
    assessment: str
    mandatory_requirements: int
    unresolved_clarifications: int
    checklist_total: int
    checklist_done: int
    last_analyzed: object | None


class DashboardResponse(BaseModel):
    tenders: list[DashboardTender]
    totals: dict[str, int]


class RequirementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement: str
    category: str
    mandatory: bool
    status: str
    evidence_type: str
    source_page: int | None
