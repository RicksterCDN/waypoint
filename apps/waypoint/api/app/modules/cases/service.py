"""Business logic for governed assurance case workflows."""

from uuid import uuid4

from ...common.repository import WaypointRepository
from ...common.tracer import trace
from ..records.service import _content_hash, _now
from .schemas import (
    ActionType,
    AssuranceCase,
    AssuranceCaseCreate,
    AssuranceCaseView,
    AuthorizedIntent,
    AuthorizeIntentCreate,
    CaseApproval,
    CaseApprovalCreate,
    CaseDraft,
    CaseDraftCreate,
    CaseRecommendation,
    CaseRecommendationCreate,
    DecisionAuditEvent,
    InvoiceAssurance,
    ProposedAction,
    ProposedActionCreate,
    RecommendationSource,
)


class CasesService:
    """Service boundary for governed case lifecycle and approvals."""

    def __init__(self, repository: WaypointRepository) -> None:
        self.repository = repository

    @trace
    async def list_action_types(self) -> list[ActionType]:
        return await self.repository.list_action_types()

    @trace
    async def list_cases(
        self,
        invoice_id: str | None = None,
        finding_id: str | None = None,
    ) -> list[AssuranceCase]:
        return await self.repository.list_cases(invoice_id=invoice_id, finding_id=finding_id)

    @trace
    async def get_case(self, case_id: str) -> AssuranceCase | None:
        return await self.repository.get_case(case_id)

    @trace
    async def list_case_recommendations(self, case_id: str) -> list[CaseRecommendation]:
        return await self.repository.list_case_recommendations(case_id)

    @trace
    async def list_case_drafts(self, case_id: str) -> list[CaseDraft]:
        return await self.repository.list_case_drafts(case_id)

    @trace
    async def get_invoice_assurance(self, invoice_id: str) -> InvoiceAssurance:
        """Join agent control-plane cases to a seed-corpus invoice for the web surface.

        Reuses the existing repository reads (no new queries) so the invoice drawer can
        fetch the linked agent decision, latest recommendation, reviewer drafts, resolved
        evidence, and grounded source URLs in a single read.
        """
        cases = await self.repository.list_cases(invoice_id=invoice_id)
        if not cases:
            return InvoiceAssurance(invoice_id=invoice_id, has_agent_decision=False, cases=[])

        evidence_by_id = {
            item.id: item for item in await self.repository.list_evidence(invoice_id=invoice_id)
        }
        views: list[AssuranceCaseView] = []
        has_agent_decision = False
        for case in cases:
            recommendations = await self.repository.list_case_recommendations(case.id)
            drafts = await self.repository.list_case_drafts(case.id)
            latest = recommendations[-1] if recommendations else None
            evidence = (
                [
                    evidence_by_id[evidence_id]
                    for evidence_id in latest.evidence_ids
                    if evidence_id in evidence_by_id
                ]
                if latest
                else []
            )
            sources = _extract_sources(latest) if latest else []
            if latest is not None:
                has_agent_decision = True
            views.append(
                AssuranceCaseView(
                    case=case,
                    latest_recommendation=latest,
                    recommendations=recommendations,
                    drafts=drafts,
                    evidence=evidence,
                    sources=sources,
                )
            )
        return InvoiceAssurance(
            invoice_id=invoice_id,
            has_agent_decision=has_agent_decision,
            cases=views,
        )

    @trace
    async def create_case(
        self,
        case_create: AssuranceCaseCreate,
        *,
        actor: str,
        auth_source: str,
    ) -> AssuranceCase:
        if case_create.idempotency_key:
            existing = await self.repository.get_case_by_idempotency_key(
                case_create.idempotency_key
            )
            if existing:
                return existing
        detail = await self.repository.get_invoice_detail(case_create.invoice_id)
        if not detail:
            raise ValueError(f"Invoice '{case_create.invoice_id}' not found.")
        finding = None
        if case_create.finding_id:
            finding = next(
                (item for item in detail.findings if item.id == case_create.finding_id),
                None,
            )
            if not finding:
                raise ValueError(
                    f"Finding '{case_create.finding_id}' does not belong to invoice "
                    f"'{case_create.invoice_id}'."
                )
        now = _now()
        title = case_create.title or (
            f"{detail.invoice_number}: {finding.category}" if finding else detail.invoice_number
        )
        assurance_case = AssuranceCase(
            id=f"case-{uuid4().hex}",
            invoice_id=case_create.invoice_id,
            finding_id=case_create.finding_id,
            title=title,
            summary=case_create.summary,
            classification=case_create.classification,
            created_at=now,
            updated_at=now,
            metadata=case_create.metadata,
            idempotency_key=case_create.idempotency_key,
        )
        await self.repository.save_case(assurance_case)
        await self._record_decision_event(
            "case_created",
            actor=actor,
            auth_source=auth_source,
            case_id=assurance_case.id,
            resource_ids={
                "invoice_id": case_create.invoice_id,
                "finding_id": case_create.finding_id,
            },
            snapshot=assurance_case.model_dump(mode="json"),
        )
        return assurance_case

    @trace
    async def create_case_recommendation(
        self,
        case_id: str,
        recommendation_create: CaseRecommendationCreate,
        *,
        actor: str,
        auth_source: str,
    ) -> CaseRecommendation:
        assurance_case = await self._require_case(case_id)
        payload = recommendation_create.model_dump(mode="json")
        now = _now()
        recommendation = CaseRecommendation(
            id=f"rec-{uuid4().hex}",
            case_id=case_id,
            version=1,
            content_hash=_content_hash(payload),
            created_by=actor,
            created_at=now,
            **recommendation_create.model_dump(),
        )
        await self.repository.save_case_recommendation(recommendation)
        await self._touch_case(assurance_case, status="investigating")
        await self._record_decision_event(
            "recommendation_created",
            actor=actor,
            auth_source=auth_source,
            case_id=case_id,
            resource_ids={"recommendation_id": recommendation.id},
            snapshot=recommendation.model_dump(mode="json"),
        )
        return recommendation

    @trace
    async def create_case_draft(
        self,
        case_id: str,
        draft_create: CaseDraftCreate,
        *,
        actor: str,
        auth_source: str,
    ) -> CaseDraft:
        assurance_case = await self._require_case(case_id)
        payload = draft_create.model_dump(mode="json")
        draft = CaseDraft(
            id=f"draft-{uuid4().hex}",
            case_id=case_id,
            version=1,
            content_hash=_content_hash(payload),
            created_by=actor,
            created_at=_now(),
            **draft_create.model_dump(),
        )
        await self.repository.save_case_draft(draft)
        await self._touch_case(assurance_case, status="investigating")
        await self._record_decision_event(
            "draft_created",
            actor=actor,
            auth_source=auth_source,
            case_id=case_id,
            resource_ids={"draft_id": draft.id},
            snapshot=draft.model_dump(mode="json"),
        )
        return draft

    @trace
    async def create_proposed_action(
        self,
        case_id: str,
        action_create: ProposedActionCreate,
        *,
        actor: str,
        auth_source: str,
    ) -> ProposedAction:
        assurance_case = await self._require_case(case_id)
        if not await self.repository.get_action_type(action_create.action_type_id):
            raise ValueError(f"Action type '{action_create.action_type_id}' is not supported.")
        if action_create.draft_id:
            draft = await self.repository.get_case_draft(action_create.draft_id)
            if not draft or draft.case_id != case_id:
                raise ValueError(
                    f"Draft '{action_create.draft_id}' does not belong to case '{case_id}'."
                )
        payload = action_create.model_dump(mode="json")
        action = ProposedAction(
            id=f"action-{uuid4().hex}",
            case_id=case_id,
            content_hash=_content_hash(payload),
            created_by=actor,
            created_at=_now(),
            **action_create.model_dump(),
        )
        await self.repository.save_proposed_action(action)
        await self._touch_case(assurance_case, status="pending_approval")
        await self._record_decision_event(
            "action_proposed",
            actor=actor,
            auth_source=auth_source,
            case_id=case_id,
            resource_ids={"proposed_action_id": action.id},
            snapshot=action.model_dump(mode="json"),
        )
        return action

    @trace
    async def create_case_approval(
        self,
        case_id: str,
        approval_create: CaseApprovalCreate,
        *,
        actor: str,
        auth_source: str,
    ) -> CaseApproval:
        assurance_case = await self._require_case(case_id)
        if approval_create.idempotency_key:
            existing = await self.repository.get_case_approval_by_idempotency_key(
                case_id,
                approval_create.idempotency_key,
            )
            if existing:
                return existing
        action = await self.repository.get_proposed_action(approval_create.proposed_action_id)
        if not action or action.case_id != case_id:
            raise ValueError(
                f"Proposed action '{approval_create.proposed_action_id}' does not belong to case "
                f"'{case_id}'."
            )
        await self._validate_approval_artifact(case_id, action, approval_create)
        approval = CaseApproval(
            id=f"approval-{uuid4().hex}",
            case_id=case_id,
            approved_by=actor,
            approved_at=_now(),
            **approval_create.model_dump(),
        )
        await self.repository.save_case_approval(approval)
        action.status = "approved" if approval.decision == "approved" else "rejected"
        await self.repository.save_proposed_action(action)
        await self._touch_case(assurance_case, status="pending_approval")
        await self._record_decision_event(
            "approval_recorded",
            actor=actor,
            auth_source=auth_source,
            case_id=case_id,
            resource_ids={"approval_id": approval.id, "proposed_action_id": action.id},
            snapshot=approval.model_dump(mode="json"),
        )
        return approval

    @trace
    async def authorize_intent(
        self,
        proposed_action_id: str,
        authorization: AuthorizeIntentCreate,
        *,
        actor: str,
        auth_source: str,
    ) -> AuthorizedIntent:
        action = await self.repository.get_proposed_action(proposed_action_id)
        if not action:
            raise ValueError(f"Proposed action '{proposed_action_id}' not found.")
        if authorization.idempotency_key:
            existing = await self.repository.get_authorized_intent_by_idempotency_key(
                proposed_action_id,
                authorization.idempotency_key,
            )
            if existing:
                return existing
        if action.status != "approved":
            raise ValueError("Only currently approved proposed actions can be authorized.")
        approval = await self._resolve_authorizing_approval(action, authorization.approval_id)
        intent = AuthorizedIntent(
            id=f"intent-{uuid4().hex}",
            case_id=action.case_id,
            proposed_action_id=action.id,
            action_type_id=action.action_type_id,
            approval_id=approval.id,
            idempotency_key=authorization.idempotency_key,
            authorized_by=actor,
            authorized_at=_now(),
            foundry_snapshot=authorization.foundry_snapshot,
            metadata=authorization.metadata,
        )
        await self.repository.save_authorized_intent(intent)
        action.status = "authorized"
        action.authorized_intent_id = intent.id
        await self.repository.save_proposed_action(action)
        assurance_case = await self._require_case(action.case_id)
        await self._touch_case(assurance_case, status="authorized")
        await self._record_decision_event(
            "intent_authorized",
            actor=actor,
            auth_source=auth_source,
            case_id=action.case_id,
            resource_ids={
                "approval_id": approval.id,
                "proposed_action_id": action.id,
                "authorized_intent_id": intent.id,
            },
            snapshot=intent.model_dump(mode="json"),
        )
        return intent

    @trace
    async def list_decision_audit_events(self) -> list[DecisionAuditEvent]:
        return await self.repository.list_decision_audit_events()

    async def _require_case(self, case_id: str) -> AssuranceCase:
        assurance_case = await self.repository.get_case(case_id)
        if not assurance_case:
            raise ValueError(f"Assurance case '{case_id}' not found.")
        return assurance_case

    async def _touch_case(self, assurance_case: AssuranceCase, *, status: str) -> None:
        assurance_case.status = status  # type: ignore[assignment]
        assurance_case.updated_at = _now()
        await self.repository.save_case(assurance_case)

    async def _validate_approval_artifact(
        self,
        case_id: str,
        action: ProposedAction,
        approval_create: CaseApprovalCreate,
    ) -> None:
        if action.draft_id and not approval_create.artifact_id:
            raise ValueError("Draft-backed actions require an approved artifact id and hash.")
        if not approval_create.artifact_id:
            return
        if action.draft_id and approval_create.artifact_id != action.draft_id:
            raise ValueError("Approval artifact must match the proposed action draft.")
        if action.draft_id and (
            approval_create.artifact_version is None
            or approval_create.artifact_content_hash is None
        ):
            raise ValueError("Draft-backed approvals require artifact version and content hash.")
        if approval_create.artifact_version is not None and approval_create.artifact_version <= 0:
            raise ValueError("Approval artifact version must be positive.")
        if (
            approval_create.artifact_content_hash is not None
            and not approval_create.artifact_content_hash
        ):
            raise ValueError("Approval artifact content hash cannot be empty.")
        draft = await self.repository.get_case_draft(approval_create.artifact_id)
        if not draft or draft.case_id != case_id:
            raise ValueError(
                f"Artifact '{approval_create.artifact_id}' does not belong to case '{case_id}'."
            )
        if (
            approval_create.artifact_version is not None
            and approval_create.artifact_version != draft.version
        ):
            raise ValueError(
                "Approval artifact version does not match the current immutable draft."
            )
        if (
            approval_create.artifact_content_hash is not None
            and approval_create.artifact_content_hash != draft.content_hash
        ):
            raise ValueError("Approval artifact content hash does not match the approved draft.")

    async def _resolve_authorizing_approval(
        self,
        action: ProposedAction,
        approval_id: str | None,
    ) -> CaseApproval:
        approvals = await self.repository.list_case_approvals(action.case_id)
        if approval_id:
            approval = await self.repository.get_case_approval(approval_id)
            if not approval:
                raise ValueError(f"Approval '{approval_id}' not found.")
            approvals = [approval]
        matching = [
            approval
            for approval in approvals
            if approval.proposed_action_id == action.id and approval.decision == "approved"
        ]
        if not matching:
            raise ValueError("A matching approved approval is required before authorizing intent.")
        return matching[-1]

    async def _record_decision_event(
        self,
        event_type: str,
        *,
        actor: str,
        auth_source: str,
        case_id: str | None,
        resource_ids: dict[str, object],
        snapshot: dict[str, object],
    ) -> None:
        await self.repository.save_decision_audit_event(
            DecisionAuditEvent(
                id=f"decision-audit-{uuid4().hex}",
                case_id=case_id,
                event_type=event_type,  # type: ignore[arg-type]
                actor=actor,
                auth_source=auth_source,
                resource_ids=resource_ids,
                snapshot=snapshot,
                created_at=_now(),
            )
        )


def _extract_sources(recommendation: CaseRecommendation) -> list[RecommendationSource]:
    """Flatten grounded source claims from a recommendation's per-expert evidence."""
    expert_evidence = recommendation.metadata.get("expert_evidence")
    if not isinstance(expert_evidence, list):
        return []
    sources: list[RecommendationSource] = []
    for lane in expert_evidence:
        if not isinstance(lane, dict):
            continue
        agent = lane.get("agent")
        plane = lane.get("plane")
        for item in lane.get("evidence", []) or []:
            if not isinstance(item, dict):
                continue
            source_ref = item.get("source_ref")
            if not source_ref:
                continue
            confidence = item.get("confidence")
            sources.append(
                RecommendationSource(
                    agent=agent if isinstance(agent, str) else None,
                    plane=plane if isinstance(plane, str) else None,
                    claim=item.get("claim"),
                    supports=item.get("supports"),
                    confidence=confidence if isinstance(confidence, (int, float)) else None,
                    source_ref=str(source_ref),
                    classification=item.get("classification"),
                )
            )
    return sources
