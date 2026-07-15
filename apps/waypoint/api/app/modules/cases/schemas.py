"""Pydantic schemas for governed assurance case workflows."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from ..records.schemas import Classification, EvidenceReference, JsonObject

CaseStatus = Literal["open", "investigating", "pending_approval", "authorized", "closed"]
RecommendationDecision = Literal["approve", "recover", "escalate", "review"]
DraftType = Literal["supplier_dispute", "escalation_packet", "approval_summary"]
ProposedActionStatus = Literal["proposed", "approved", "authorized", "rejected", "cancelled"]
ApprovalDecision = Literal["approved", "rejected"]
DecisionAuditEventType = Literal[
    "case_created",
    "recommendation_created",
    "draft_created",
    "action_proposed",
    "approval_recorded",
    "intent_authorized",
]


class ActionType(BaseModel):
    id: str
    name: str
    description: str
    required_role: str = "admin"
    approval_gate: str = "finance"
    external_side_effect: bool = False
    metadata: JsonObject = Field(default_factory=dict)


class AssuranceCaseCreate(BaseModel):
    invoice_id: str
    finding_id: str | None = None
    title: str | None = None
    summary: str = ""
    classification: Classification = "standard"
    metadata: JsonObject = Field(default_factory=dict)
    # Optional caller-supplied key so creating the same logical case twice
    # (e.g. a run opened at batch enroll and re-created at completion) returns
    # the existing case instead of duplicating it.
    idempotency_key: str | None = None


class AssuranceCase(BaseModel):
    id: str
    invoice_id: str
    finding_id: str | None = None
    title: str
    summary: str = ""
    status: CaseStatus = "open"
    classification: Classification = "standard"
    created_at: datetime
    updated_at: datetime
    metadata: JsonObject = Field(default_factory=dict)
    idempotency_key: str | None = None


class CaseRecommendationCreate(BaseModel):
    decision: RecommendationDecision
    reasoning: str
    confidence: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    money_at_risk: Decimal = Decimal("0")
    evidence_ids: list[str] = Field(default_factory=list)
    proposed_next_actions: list[str] = Field(default_factory=list)
    foundry_response_id: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class CaseRecommendation(BaseModel):
    id: str
    case_id: str
    version: int = 1
    decision: RecommendationDecision
    reasoning: str
    confidence: Decimal = Decimal("0")
    money_at_risk: Decimal = Decimal("0")
    evidence_ids: list[str] = Field(default_factory=list)
    proposed_next_actions: list[str] = Field(default_factory=list)
    content_hash: str
    created_by: str
    created_at: datetime
    foundry_response_id: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class CaseDraftCreate(BaseModel):
    draft_type: DraftType
    title: str
    body: str
    source_recommendation_id: str | None = None
    classification: Classification = "standard"
    metadata: JsonObject = Field(default_factory=dict)


class CaseDraft(BaseModel):
    id: str
    case_id: str
    version: int = 1
    draft_type: DraftType
    title: str
    body: str
    source_recommendation_id: str | None = None
    classification: Classification = "standard"
    content_hash: str
    created_by: str
    created_at: datetime
    metadata: JsonObject = Field(default_factory=dict)


class ProposedActionCreate(BaseModel):
    action_type_id: str
    title: str
    description: str = ""
    draft_id: str | None = None
    recommendation_id: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ProposedAction(BaseModel):
    id: str
    case_id: str
    action_type_id: str
    title: str
    description: str = ""
    status: ProposedActionStatus = "proposed"
    draft_id: str | None = None
    recommendation_id: str | None = None
    content_hash: str
    created_by: str
    created_at: datetime
    authorized_intent_id: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class CaseApprovalCreate(BaseModel):
    proposed_action_id: str
    decision: ApprovalDecision
    justification: str = ""
    artifact_id: str | None = None
    artifact_version: int | None = None
    artifact_content_hash: str | None = None
    idempotency_key: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class CaseApproval(BaseModel):
    id: str
    case_id: str
    proposed_action_id: str
    decision: ApprovalDecision
    justification: str = ""
    artifact_id: str | None = None
    artifact_version: int | None = None
    artifact_content_hash: str | None = None
    idempotency_key: str | None = None
    approved_by: str
    approved_at: datetime
    metadata: JsonObject = Field(default_factory=dict)


class AuthorizedIntent(BaseModel):
    id: str
    case_id: str
    proposed_action_id: str
    action_type_id: str
    status: str = "authorized"
    approval_id: str
    idempotency_key: str | None = None
    authorized_by: str
    authorized_at: datetime
    foundry_snapshot: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)


class AuthorizeIntentCreate(BaseModel):
    approval_id: str | None = None
    idempotency_key: str | None = None
    foundry_snapshot: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)


class DecisionAuditEvent(BaseModel):
    id: str
    case_id: str | None = None
    event_type: DecisionAuditEventType
    actor: str
    auth_source: str
    resource_ids: JsonObject = Field(default_factory=dict)
    snapshot: JsonObject = Field(default_factory=dict)
    created_at: datetime


class RecommendationSource(BaseModel):
    """A single grounded source claim flattened from a recommendation's expert evidence."""

    agent: str | None = None
    plane: str | None = None
    claim: str | None = None
    supports: str | None = None
    confidence: float | None = None
    source_ref: str
    classification: str | None = None


class AssuranceCaseView(BaseModel):
    """An agent case joined to its latest recommendation, drafts, evidence, and sources."""

    case: AssuranceCase
    latest_recommendation: CaseRecommendation | None = None
    recommendations: list[CaseRecommendation] = Field(default_factory=list)
    drafts: list[CaseDraft] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    sources: list[RecommendationSource] = Field(default_factory=list)


class InvoiceAssurance(BaseModel):
    """Agent control-plane decision(s) linked to a seed-corpus invoice."""

    invoice_id: str
    has_agent_decision: bool = False
    cases: list[AssuranceCaseView] = Field(default_factory=list)
