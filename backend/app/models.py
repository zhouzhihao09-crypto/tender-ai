from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class AnalysisStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TenderStatus(StrEnum):
    NEW = "New"
    REVIEWING = "Reviewing"
    BID = "Bid"
    NO_BID = "No-Bid"
    PREPARING = "Preparing"
    SUBMITTED = "Submitted"
    CLOSED = "Closed"


class RiskLevel(StrEnum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(default=True)
    plan: Mapped[str] = mapped_column(String(40), default="FREE")
    subscription_status: Mapped[str] = mapped_column(String(40), default="NOT_CONFIGURED")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(160), default="Main workspace")
    slug: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True, index=True)
    tender_id: Mapped[int | None] = mapped_column(ForeignKey("tenders.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80))
    usage_type: Mapped[str] = mapped_column(String(80))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("provider", "provider_subscription_id", name="uq_subscription_provider_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="stripe")
    provider_customer_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    provider_subscription_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    provider_price_id: Mapped[str] = mapped_column(String(120))
    plan: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(default=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BillingEvent(Base):
    __tablename__ = "billing_events"
    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_billing_event_provider_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), default="stripe")
    provider_event_id: Mapped[str] = mapped_column(String(160), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    payload_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)


class Tender(Base):
    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    organization: Mapped[str | None] = mapped_column(String(240), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default=TenderStatus.NEW)
    risk: Mapped[str] = mapped_column(String(20), default=RiskLevel.MEDIUM)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    file_name: Mapped[str] = mapped_column(String(255))
    stored_file_name: Mapped[str] = mapped_column(String(255), unique=True)
    analysis_status: Mapped[str] = mapped_column(String(30), default=AnalysisStatus.QUEUED)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    analysis_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    requirements: Mapped[list["TenderRequirement"]] = relationship(cascade="all, delete-orphan")
    sources: Mapped[list["TenderSource"]] = relationship(cascade="all, delete-orphan")
    documents: Mapped[list["TenderDocument"]] = relationship(cascade="all, delete-orphan")
    dates: Mapped[list["TenderDate"]] = relationship(cascade="all, delete-orphan")
    risks: Mapped[list["TenderRisk"]] = relationship(cascade="all, delete-orphan")
    questions: Mapped[list["TenderQuestion"]] = relationship(cascade="all, delete-orphan")
    checklist_items: Mapped[list["TenderChecklistItem"]] = relationship(cascade="all, delete-orphan")


class TenderRequirement(Base):
    __tablename__ = "tender_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"))
    requirement: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), default="Administrative")
    mandatory: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(30), default="Not Checked")
    evidence_type: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)


class TenderSource(Base):
    __tablename__ = "tender_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"))
    page: Mapped[int] = mapped_column(Integer)
    snippet: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    chunk_id: Mapped[str] = mapped_column(String(80), default="")
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    retrieval_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    embedding_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)


class TenderDocument(Base):
    __tablename__ = "tender_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"))
    name: Mapped[str] = mapped_column(Text)
    required: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(30), default="NOT_STARTED")
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(20), default="UNKNOWN")


class TenderDate(Base):
    __tablename__ = "tender_dates"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"))
    event: Mapped[str] = mapped_column(String(120))
    date_value: Mapped[str] = mapped_column(String(80))
    time_value: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(20), default="EXPLICIT")


class TenderRisk(Base):
    __tablename__ = "tender_risks"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"))
    title: Mapped[str] = mapped_column(String(160))
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    explanation: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str] = mapped_column(Text)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(20), default="INFERENCE")


class TenderQuestion(Base):
    __tablename__ = "tender_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"))
    question: Mapped[str] = mapped_column(Text)
    why_it_matters: Mapped[str] = mapped_column(Text)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)


class TenderChecklistItem(Base):
    __tablename__ = "tender_checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="NOT_STARTED")
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CompanyProfile(Base):
    __tablename__ = "company_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True, unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    company_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(160), nullable=True)
    years_in_business: Mapped[int | None] = mapped_column(Integer, nullable=True)
    main_services: Mapped[str | None] = mapped_column(Text, nullable=True)
    areas_of_expertise: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevant_licences: Mapped[str | None] = mapped_column(Text, nullable=True)
    certifications: Mapped[str | None] = mapped_column(Text, nullable=True)
    registrations: Mapped[str | None] = mapped_column(Text, nullable=True)
    bca_registration_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    past_project_experience: Mapped[str | None] = mapped_column(Text, nullable=True)
    typical_project_size: Mapped[str | None] = mapped_column(String(160), nullable=True)
    maximum_project_size: Mapped[str | None] = mapped_column(String(160), nullable=True)
    geographic_coverage: Mapped[str | None] = mapped_column(Text, nullable=True)
    number_of_employees: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key_capabilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    insurance_coverage: Mapped[str | None] = mapped_column(Text, nullable=True)
    other_qualifications: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TenderAssessment(Base):
    __tablename__ = "tender_assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    company_profile_version: Mapped[int] = mapped_column(Integer)
    recommendation: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[str] = mapped_column(String(20))
    readiness_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explanation: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TenderRequirementMatch(Base):
    __tablename__ = "tender_requirement_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("tender_assessments.id"), index=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("tender_requirements.id"))
    match_status: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[str] = mapped_column(String(20))
    company_field: Mapped[str | None] = mapped_column(String(80), nullable=True)
    company_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_explanation: Mapped[str] = mapped_column(Text)
    critical: Mapped[bool] = mapped_column(default=False)
    evidence_document_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class TenderBidAction(Base):
    __tablename__ = "tender_bid_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("tender_assessments.id"), index=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    status: Mapped[str] = mapped_column(String(20), default="NOT_STARTED")
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_field: Mapped[str | None] = mapped_column(String(80), nullable=True)


class CompanyEvidenceDocument(Base):
    __tablename__ = "company_evidence_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255), unique=True)
    category: Mapped[str] = mapped_column(String(40), default="OTHER")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="READY")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    expiry_date: Mapped[str | None] = mapped_column(String(80), nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CompanyEvidenceChunk(Base):
    __tablename__ = "company_evidence_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("company_evidence_documents.id"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)


class TenderRequirementEvidence(Base):
    __tablename__ = "tender_requirement_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("tender_requirements.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("company_evidence_documents.id"), index=True)
    chunk_id: Mapped[str] = mapped_column(String(80))
    page: Mapped[int] = mapped_column(Integer)
    snippet: Mapped[str] = mapped_column(Text)
    relevance: Mapped[str] = mapped_column(String(20), default="INFERENCE")


class BidWorkspace(Base):
    __tablename__ = "bid_workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), unique=True, index=True)
    workflow_status: Mapped[str] = mapped_column(String(30), default="BID_DECISION_PENDING")
    decision: Mapped[str] = mapped_column(String(20), default="PENDING")
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BidRequirement(Base):
    __tablename__ = "bid_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("bid_workspaces.id"), index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("tender_requirements.id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="NOT_STARTED")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class BidDocument(Base):
    __tablename__ = "bid_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("bid_workspaces.id"), index=True)
    tender_document_id: Mapped[int] = mapped_column(ForeignKey("tender_documents.id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="MISSING")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class BidDocumentLink(Base):
    __tablename__ = "bid_document_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    bid_document_id: Mapped[int] = mapped_column(ForeignKey("bid_documents.id"), index=True)
    evidence_document_id: Mapped[int] = mapped_column(ForeignKey("company_evidence_documents.id"), index=True)


class BidClarification(Base):
    __tablename__ = "bid_clarifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("bid_workspaces.id"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("tender_questions.id"), unique=True)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)


class BidNote(Base):
    __tablename__ = "bid_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("bid_workspaces.id"), index=True)
    scope: Mapped[str] = mapped_column(String(30), default="TENDER")
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BidPackage(Base):
    __tablename__ = "bid_packages"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("bid_workspaces.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="NOT_STARTED")
    review_status: Mapped[str] = mapped_column(String(30), default="NOT_REVIEWED")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BidPackageItem(Base):
    __tablename__ = "bid_package_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("bid_packages.id"), index=True)
    bid_document_id: Mapped[int | None] = mapped_column(ForeignKey("bid_documents.id"), nullable=True)
    evidence_document_id: Mapped[int | None] = mapped_column(ForeignKey("company_evidence_documents.id"), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    included: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(String(24), default="MISSING")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class BidPackageExport(Base):
    __tablename__ = "bid_package_exports"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("bid_packages.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    readiness_state: Mapped[str] = mapped_column(String(30))
    included_count: Mapped[int] = mapped_column(Integer)
    incomplete: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
