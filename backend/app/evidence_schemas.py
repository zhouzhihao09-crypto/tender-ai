from datetime import datetime
from pydantic import BaseModel, ConfigDict


class EvidenceDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    category: str
    description: str | None
    status: str
    page_count: int
    expiry_date: str | None
    created_at: datetime


class EvidenceMetadataUpdate(BaseModel):
    category: str | None = None
    description: str | None = None
    expiry_date: str | None = None


class EvidenceSearchResult(BaseModel):
    document_id: int
    filename: str
    category: str
    page: int
    chunk_id: str
    snippet: str
    score: float


class RequirementEvidenceRead(BaseModel):
    document_id: int
    filename: str
    page: int
    chunk_id: str
    snippet: str
    relevance: str