"""Pydantic schemas for derived operational work views."""

from decimal import Decimal

from pydantic import BaseModel, Field

from ..cases.schemas import ActionType, AssuranceCase
from ..records.schemas import (
    Classification,
    ContractDocumentDetail,
    InvoiceDetail,
    JsonObject,
    PolicyDetail,
)


class WorkQueueItem(BaseModel):
    invoice_id: str
    invoice_number: str
    supplier_id: str
    supplier_name: str
    finding_id: str | None = None
    case_id: str | None = None
    severity: str
    status: str
    category: str
    summary: str
    currency: str = "USD"
    money_at_risk: Decimal = Decimal("0")
    classification: Classification = "standard"


class ContextRedaction(BaseModel):
    section: str
    reason: str
    classification: Classification


class InvoiceContextBundle(BaseModel):
    invoice: InvoiceDetail
    contract_documents: list[ContractDocumentDetail] = Field(default_factory=list)
    policies: list[PolicyDetail] = Field(default_factory=list)
    cases: list[AssuranceCase] = Field(default_factory=list)
    allowed_actions: list[ActionType] = Field(default_factory=list)
    redactions: list[ContextRedaction] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
