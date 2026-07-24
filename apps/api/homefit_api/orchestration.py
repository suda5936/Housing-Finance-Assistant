import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from functools import partial
from typing import TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from homefit_api.costs import (
    CalculationStatus,
    CostCalculationInput,
    CostCalculationResponse,
    ScenarioKind,
    calculate_costs,
)
from homefit_api.data import Currency, DataRepository, SessionExport
from homefit_api.documents import (
    DocumentService,
    DocumentStatus,
)
from homefit_api.policies import EligibilityInput, PolicyRegistry, ReviewStatus
from homefit_api.rag import (
    Citation,
    EvidenceStatus,
    GroundedEligibilityResult,
    PolicyEvidenceRegistry,
    ground_eligibility,
)
from homefit_api.ranking import (
    Criterion,
    RankingRequest,
    RankingResponse,
    RankingStatus,
    rank_candidates,
)

T = TypeVar("T", bound=BaseModel)
FINAL_STATES = frozenset({"decision_card", "official_check", "failed"})
HUMAN_STATES = frozenset(
    {"profile", "candidate_input", "user_confirmation", "clarification"}
)
ORCHESTRATION_VERSION = "agent-state-machine-v1"
CHECKLIST_DISCLAIMER = (
    "이 항목은 계약 전 사실 확인을 돕기 위한 안내이며 법률 판단이나 계약 안전을 보장하지 않습니다."
)


class AgentState(StrEnum):
    CONSENT = "consent"
    PROFILE = "profile"
    CANDIDATE_INPUT = "candidate_input"
    EXTRACTION = "extraction"
    USER_CONFIRMATION = "user_confirmation"
    MISSING_INFO_CHECK = "missing_info_check"
    CLARIFICATION = "clarification"
    ELIGIBILITY = "eligibility"
    COST_CALCULATION = "cost_calculation"
    RANKING = "ranking"
    VERIFICATION = "verification"
    DECISION_CARD = "decision_card"
    OFFICIAL_CHECK = "official_check"
    FAILED = "failed"


class ValueSource(StrEnum):
    DOCUMENT_CONFIRMED = "document_confirmed"
    USER = "user"


class ToolCallStatus(StrEnum):
    COMPLETED = "completed"
    CACHED = "cached"
    FAILED = "failed"
    MANUAL_FALLBACK = "manual_fallback"


class AgentLimits(BaseModel):
    max_steps: int = Field(default=20, ge=1, le=100)
    max_tool_calls: int = Field(default=20, ge=1, le=100)
    max_retries_per_tool: int = Field(default=1, ge=0, le=3)
    tool_timeout_seconds: int = Field(default=30, ge=1, le=120)
    llm_call_limit: int = Field(default=0, ge=0, le=10)
    llm_token_budget: int = Field(default=0, ge=0, le=32_768)


class AgentUsage(BaseModel):
    steps: int = 0
    tool_calls: int = 0
    cache_hits: int = 0
    retries: int = 0
    llm_calls: int = 0
    llm_tokens: int = 0
    estimated_cost_krw: Decimal = Decimal("0")


class ResolvedValue(BaseModel):
    value: str
    source: ValueSource
    updated_at: datetime


class CandidateCostRequest(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=80)
    input: CostCalculationInput


class AgentContext(BaseModel):
    document_id: UUID | None = None
    document_candidate_id: str | None = Field(default=None, max_length=80)
    use_manual_candidate_entry: bool = False
    policy_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$", max_length=80)
    eligibility_input: EligibilityInput | None = None
    cost_requests: list[CandidateCostRequest] = Field(default_factory=list, max_length=3)
    ranking_request: RankingRequest | None = None
    resolved_document_values: dict[str, ResolvedValue] = Field(default_factory=dict)
    scenario_revision: int = Field(default=0, ge=0)


class AgentRunCreate(BaseModel):
    context: AgentContext = Field(default_factory=AgentContext)
    limits: AgentLimits = Field(default_factory=AgentLimits)


class AgentContextPatch(BaseModel):
    document_id: UUID | None = None
    document_candidate_id: str | None = Field(default=None, max_length=80)
    use_manual_candidate_entry: bool | None = None
    policy_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$", max_length=80)
    eligibility_input: EligibilityInput | None = None
    cost_requests: list[CandidateCostRequest] | None = Field(default=None, max_length=3)
    ranking_request: RankingRequest | None = None
    user_corrections: dict[str, str] = Field(default_factory=dict, max_length=20)
    scenario_update: bool = False


class AgentQuestion(BaseModel):
    code: str
    field: str
    prompt: str
    why_needed: str
    manual_path_available: bool = True


class ToolCallRecord(BaseModel):
    sequence: int
    tool_name: str
    status: ToolCallStatus
    input_sha256: str
    output_sha256: str | None = None
    attempt: int
    duration_ms: int
    error_code: str | None = None
    created_at: datetime


class StateTransition(BaseModel):
    sequence: int
    from_state: AgentState
    to_state: AgentState
    reason_code: str
    created_at: datetime


class VerificationGate(BaseModel):
    code: str
    passed: bool
    blocking_state: AgentState | None = None
    reason: str


class ContractChecklistItem(BaseModel):
    code: str
    version: str
    action: str
    applies_when: str
    verification_actor: str
    review_status: ReviewStatus
    reviewer: str | None
    citations: list[Citation] = Field(min_length=1)
    disclaimer: str = CHECKLIST_DISCLAIMER


class DecisionCard(BaseModel):
    status: AgentState
    winner_candidate_ids: list[str]
    summary_sentences: list[str]
    warnings: list[str]
    checklist: list[ContractChecklistItem]
    disclaimer: str = (
        "이 결과는 입력값과 검토된 규칙에 따른 비교 자료이며 대출 승인, 정책 선정, "
        "계약 안전 또는 법률적 결론을 보장하지 않습니다."
    )


class AgentRun(BaseModel):
    id: UUID
    session_id: UUID
    orchestration_version: str = ORCHESTRATION_VERSION
    state: AgentState
    context: AgentContext
    questions: list[AgentQuestion]
    eligibility: GroundedEligibilityResult | None = None
    costs: dict[str, CostCalculationResponse] = Field(default_factory=dict)
    ranking: RankingResponse | None = None
    verification_gates: list[VerificationGate] = Field(default_factory=list)
    decision_card: DecisionCard | None = None
    official_check_reasons: list[str] = Field(default_factory=list)
    transitions: list[StateTransition] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    usage: AgentUsage
    limits: AgentLimits
    revision: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime


class AgentAdvanceRequest(BaseModel):
    auto_run: bool = True


class AgentRunNotFoundError(LookupError):
    pass


class AgentLimitError(RuntimeError):
    pass


@dataclass(slots=True)
class StoredAgentRun:
    run: AgentRun
    cache: dict[str, BaseModel] = field(default_factory=dict)


def _payload_sha256(payload: object) -> str:
    value: object
    if isinstance(payload, BaseModel):
        value = payload.model_dump(mode="json")
    else:
        value = payload
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def merge_resolved_values(
    current: dict[str, ResolvedValue],
    incoming: dict[str, str],
    source: ValueSource,
) -> dict[str, ResolvedValue]:
    merged = dict(current)
    now = datetime.now(UTC)
    for key, value in incoming.items():
        existing = merged.get(key)
        if existing and existing.source is ValueSource.USER and source is not ValueSource.USER:
            continue
        merged[key] = ResolvedValue(value=value, source=source, updated_at=now)
    return merged


class AgentOrchestrator:
    def __init__(
        self,
        policies: PolicyRegistry,
        evidence: PolicyEvidenceRegistry,
    ) -> None:
        self._policies = policies
        self._evidence = evidence
        self._runs: dict[UUID, StoredAgentRun] = {}

    def create_run(
        self,
        repository: DataRepository,
        documents: DocumentService,
        session_id: UUID,
        access_token: str,
        payload: AgentRunCreate,
    ) -> AgentRun:
        repository.register_cleanup_callback(self.delete_session_runs)
        session = repository.export_session(session_id, access_token)
        active_count = sum(
            stored.run.session_id == session_id and stored.run.state.value not in FINAL_STATES
            for stored in self._runs.values()
        )
        if active_count >= 3:
            raise ValueError("A session can have at most three active agent runs")
        now = datetime.now(UTC)
        run = AgentRun(
            id=uuid4(),
            session_id=session_id,
            state=AgentState.CONSENT,
            context=payload.context,
            questions=[],
            usage=AgentUsage(),
            limits=payload.limits,
            created_at=now,
            updated_at=now,
        )
        stored = StoredAgentRun(run=run)
        self._runs[run.id] = stored
        self._synchronize(stored, repository, documents, access_token, session)
        return stored.run

    def delete_session_runs(self, session_id: UUID) -> int:
        run_ids = [
            run_id for run_id, stored in self._runs.items() if stored.run.session_id == session_id
        ]
        for run_id in run_ids:
            del self._runs[run_id]
        return len(run_ids)

    def get_run(
        self,
        repository: DataRepository,
        session_id: UUID,
        access_token: str,
        run_id: UUID,
    ) -> AgentRun:
        repository.authorize_session(session_id, access_token)
        return self._stored(session_id, run_id).run

    def update_context(
        self,
        repository: DataRepository,
        documents: DocumentService,
        session_id: UUID,
        access_token: str,
        run_id: UUID,
        patch: AgentContextPatch,
    ) -> AgentRun:
        session = repository.export_session(session_id, access_token)
        stored = self._stored(session_id, run_id)
        material_fields = {
            "document_id",
            "document_candidate_id",
            "use_manual_candidate_entry",
            "policy_code",
            "eligibility_input",
            "cost_requests",
            "ranking_request",
            "user_corrections",
        }
        if (
            stored.run.state.value in FINAL_STATES
            and bool(patch.model_fields_set & material_fields)
            and not patch.scenario_update
        ):
            raise ValueError("Final run inputs require scenario_update=true")
        updates: dict[str, object] = {}
        for name in (
            "document_id",
            "document_candidate_id",
            "use_manual_candidate_entry",
            "policy_code",
            "eligibility_input",
            "cost_requests",
            "ranking_request",
        ):
            if name in patch.model_fields_set:
                updates[name] = getattr(patch, name)
        context = stored.run.context.model_copy(update=updates)
        if patch.user_corrections:
            context = context.model_copy(
                update={
                    "resolved_document_values": merge_resolved_values(
                        context.resolved_document_values,
                        patch.user_corrections,
                        ValueSource.USER,
                    )
                }
            )
        if patch.scenario_update:
            context = context.model_copy(
                update={"scenario_revision": context.scenario_revision + 1}
            )
            stored.run = stored.run.model_copy(
                update={
                    "eligibility": None,
                    "costs": {},
                    "ranking": None,
                    "verification_gates": [],
                    "decision_card": None,
                    "official_check_reasons": [],
                }
            )
            self._transition(stored, AgentState.MISSING_INFO_CHECK, "SCENARIO_UPDATED")
        stored.run = stored.run.model_copy(
            update={
                "context": context,
                "revision": stored.run.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._synchronize(stored, repository, documents, access_token, session)
        return stored.run

    def advance(
        self,
        repository: DataRepository,
        documents: DocumentService,
        session_id: UUID,
        access_token: str,
        run_id: UUID,
        request: AgentAdvanceRequest,
    ) -> AgentRun:
        session = repository.export_session(session_id, access_token)
        stored = self._stored(session_id, run_id)
        self._synchronize(stored, repository, documents, access_token, session)
        while stored.run.state.value not in FINAL_STATES:
            if stored.run.state.value in HUMAN_STATES:
                break
            self._step(stored, repository, documents, access_token, session)
            if not request.auto_run:
                break
        return stored.run

    def _stored(self, session_id: UUID, run_id: UUID) -> StoredAgentRun:
        stored = self._runs.get(run_id)
        if stored is None or stored.run.session_id != session_id:
            raise AgentRunNotFoundError(run_id)
        return stored

    def _synchronize(
        self,
        stored: StoredAgentRun,
        repository: DataRepository,
        documents: DocumentService,
        access_token: str,
        session: SessionExport,
    ) -> None:
        if stored.run.state.value in FINAL_STATES:
            return
        if session.profile is None:
            self._pause(
                stored,
                AgentState.PROFILE,
                AgentQuestion(
                    code="PROFILE_REQUIRED",
                    field="profile",
                    prompt="월소득과 사용 가능한 보증금 등 기본 정보를 입력해 주세요.",
                    why_needed="비용 계산과 후보 적합성 확인에 필요합니다.",
                ),
            )
            return
        if len(session.candidates) < 2:
            self._pause(
                stored,
                AgentState.CANDIDATE_INPUT,
                AgentQuestion(
                    code="TWO_CANDIDATES_REQUIRED",
                    field="candidates",
                    prompt="비교할 주거 후보를 두 개 이상 입력해 주세요.",
                    why_needed="순위 비교에는 최소 두 후보가 필요합니다.",
                ),
            )
            return
        context = stored.run.context
        if context.document_id and not context.use_manual_candidate_entry:
            analysis = documents.get_analysis(
                repository, stored.run.session_id, access_token, context.document_id
            )
            if analysis.document.status is DocumentStatus.STORED:
                self._transition(stored, AgentState.EXTRACTION, "DOCUMENT_READY_FOR_EXTRACTION")
                return
            if analysis.document.status is DocumentStatus.MANUAL_REQUIRED:
                self._record_manual_fallback(stored, "document.extract")
                self._pause(
                    stored,
                    AgentState.CLARIFICATION,
                    AgentQuestion(
                        code="DOCUMENT_MANUAL_ENTRY_REQUIRED",
                        field="use_manual_candidate_entry",
                        prompt="문서 자동 추출이 불가능합니다. 후보 정보를 직접 입력해 주세요.",
                        why_needed="확인되지 않은 OCR 값을 계산에 사용할 수 없습니다.",
                    ),
                )
                return
            confirmed = documents.confirmed_fields(
                repository, stored.run.session_id, access_token, context.document_id
            )
            context = context.model_copy(
                update={
                    "resolved_document_values": merge_resolved_values(
                        context.resolved_document_values,
                        confirmed.values,
                        ValueSource.DOCUMENT_CONFIRMED,
                    )
                }
            )
            stored.run = stored.run.model_copy(update={"context": context})
            if not confirmed.ready_for_calculation:
                self._pause(
                    stored,
                    AgentState.USER_CONFIRMATION,
                    AgentQuestion(
                        code="DOCUMENT_FIELDS_REQUIRE_CONFIRMATION",
                        field=confirmed.missing_confirmed_fields[0],
                        prompt=(
                            f"문서의 {confirmed.missing_confirmed_fields[0]} 값을 확인하거나 "
                            "직접 입력해 주세요."
                        ),
                        why_needed="사용자가 확정하지 않은 문서 값은 계산에 사용할 수 없습니다.",
                    ),
                )
                return
        self._transition(stored, AgentState.MISSING_INFO_CHECK, "SESSION_CONTEXT_SYNCHRONIZED")
        question = self._next_missing_question(stored.run.context, session)
        if question:
            self._pause(stored, AgentState.CLARIFICATION, question)
        else:
            self._transition(stored, AgentState.ELIGIBILITY, "REQUIRED_INPUTS_PRESENT")

    def _next_missing_question(
        self,
        context: AgentContext,
        session: SessionExport,
    ) -> AgentQuestion | None:
        if context.policy_code is None:
            return AgentQuestion(
                code="POLICY_REQUIRED",
                field="policy_code",
                prompt="확인할 주거지원 정책을 선택해 주세요.",
                why_needed="정책 규칙과 공식 근거 버전을 선택해야 합니다.",
            )
        if context.eligibility_input is None:
            return AgentQuestion(
                code="ELIGIBILITY_INPUT_REQUIRED",
                field="eligibility_input",
                prompt="선택한 정책의 자격 확인 정보를 입력해 주세요.",
                why_needed="누락값을 탈락으로 간주하지 않고 정확히 판정하기 위해 필요합니다.",
            )
        candidate_ids = {str(candidate.id) for candidate in session.candidates}
        if (
            context.document_id
            and not context.use_manual_candidate_entry
            and context.document_candidate_id not in candidate_ids
        ):
            return AgentQuestion(
                code="DOCUMENT_CANDIDATE_REQUIRED",
                field="document_candidate_id",
                prompt="업로드한 계약서가 어느 후보의 문서인지 선택해 주세요.",
                why_needed="문서 확정값과 해당 후보의 계산 입력을 교차검증해야 합니다.",
            )
        cost_ids = {item.candidate_id for item in context.cost_requests}
        missing_costs = sorted(candidate_ids - cost_ids)
        if missing_costs:
            return AgentQuestion(
                code="COST_INPUT_REQUIRED",
                field=f"cost_requests.{missing_costs[0]}",
                prompt="각 후보의 금리 범위, 초기비용과 통근비를 확인해 주세요.",
                why_needed="확인되지 않은 금리나 비용을 임의로 가정하지 않습니다.",
            )
        if context.ranking_request is None:
            return AgentQuestion(
                code="RANKING_INPUT_REQUIRED",
                field="ranking_request",
                prompt="후보별 위험 근거와 비교 가중치를 확인해 주세요.",
                why_needed="에이전트가 선호도나 위험점수를 임의로 만들 수 없습니다.",
            )
        return None

    def _step(
        self,
        stored: StoredAgentRun,
        repository: DataRepository,
        documents: DocumentService,
        access_token: str,
        session: SessionExport,
    ) -> None:
        usage = stored.run.usage
        if usage.steps >= stored.run.limits.max_steps:
            self._fail(stored, "MAX_STEPS_EXCEEDED")
            return
        stored.run = stored.run.model_copy(
            update={"usage": usage.model_copy(update={"steps": usage.steps + 1})}
        )
        state = stored.run.state
        if state is AgentState.EXTRACTION:
            document_id = stored.run.context.document_id
            if document_id is None:
                self._transition(stored, AgentState.MISSING_INFO_CHECK, "DOCUMENT_SKIPPED")
                return
            try:
                self._execute_tool(
                    stored,
                    "document.extract",
                    {"document_id": str(document_id)},
                    lambda: documents.extract(
                        repository, stored.run.session_id, access_token, document_id
                    ),
                )
            except Exception:
                self._record_manual_fallback(stored, "document.extract")
            self._synchronize(stored, repository, documents, access_token, session)
            return
        if state is AgentState.MISSING_INFO_CHECK:
            question = self._next_missing_question(stored.run.context, session)
            if question:
                self._pause(stored, AgentState.CLARIFICATION, question)
            else:
                self._transition(stored, AgentState.ELIGIBILITY, "REQUIRED_INPUTS_PRESENT")
            return
        if state is AgentState.ELIGIBILITY:
            self._run_eligibility(stored)
            return
        if state is AgentState.COST_CALCULATION:
            self._run_costs(stored)
            return
        if state is AgentState.RANKING:
            self._run_ranking(stored)
            return
        if state is AgentState.VERIFICATION:
            self._verify(stored)
            return
        self._fail(stored, "INVALID_AUTOMATION_STATE")

    def _run_eligibility(self, stored: StoredAgentRun) -> None:
        context = stored.run.context
        if context.policy_code is None or context.eligibility_input is None:
            self._transition(stored, AgentState.MISSING_INFO_CHECK, "ELIGIBILITY_INPUT_LOST")
            return
        eligibility_input = context.eligibility_input
        try:
            policy = self._policies.get(context.policy_code)
            result = self._execute_tool(
                stored,
                "policy.eligibility_with_evidence",
                {"policy_code": context.policy_code, "input": eligibility_input},
                lambda: ground_eligibility(policy, eligibility_input, self._evidence),
            )
        except Exception:
            self._official_check(stored, "POLICY_TOOL_FAILED")
            return
        stored.run = stored.run.model_copy(update={"eligibility": result})
        self._transition(stored, AgentState.COST_CALCULATION, "ELIGIBILITY_RECORDED")

    def _run_costs(self, stored: StoredAgentRun) -> None:
        results: dict[str, CostCalculationResponse] = {}
        try:
            for item in stored.run.context.cost_requests:
                cost_input = item.input
                result = self._execute_tool(
                    stored,
                    "cost.calculate",
                    cost_input,
                    partial(calculate_costs, cost_input),
                )
                results[item.candidate_id] = result
        except Exception:
            self._pause(
                stored,
                AgentState.CLARIFICATION,
                AgentQuestion(
                    code="COST_TOOL_FAILED",
                    field="cost_requests",
                    prompt="비용 입력을 다시 확인해 주세요.",
                    why_needed="비용 계산 도구가 검증된 결과를 반환하지 못했습니다.",
                ),
            )
            return
        stored.run = stored.run.model_copy(update={"costs": results})
        missing = [
            candidate_id
            for candidate_id, result in results.items()
            if result.status is not CalculationStatus.CALCULATED
        ]
        if missing:
            self._pause(
                stored,
                AgentState.CLARIFICATION,
                AgentQuestion(
                    code="COST_RESULT_INCOMPLETE",
                    field=f"cost_requests.{missing[0]}",
                    prompt="관리비 또는 통근비 범위를 보완해 주세요.",
                    why_needed="불완전한 비용 결과는 순위 계산에 사용할 수 없습니다.",
                ),
            )
            return
        self._transition(stored, AgentState.RANKING, "COSTS_CALCULATED")

    def _run_ranking(self, stored: StoredAgentRun) -> None:
        request = stored.run.context.ranking_request
        if request is None:
            self._transition(stored, AgentState.MISSING_INFO_CHECK, "RANKING_INPUT_LOST")
            return
        try:
            result = self._execute_tool(
                stored,
                "ranking.compare",
                request,
                lambda: rank_candidates(request),
            )
        except Exception:
            self._pause(
                stored,
                AgentState.CLARIFICATION,
                AgentQuestion(
                    code="RANKING_TOOL_FAILED",
                    field="ranking_request",
                    prompt="후보 비교 입력과 가중치를 다시 확인해 주세요.",
                    why_needed="순위 계산 도구가 검증된 결과를 반환하지 못했습니다.",
                ),
            )
            return
        stored.run = stored.run.model_copy(update={"ranking": result})
        if result.status is not RankingStatus.RANKED:
            self._pause(
                stored,
                AgentState.CLARIFICATION,
                AgentQuestion(
                    code="RANKING_NOT_COMPLETE",
                    field="ranking_request",
                    prompt="비교에 필요한 후보별 비용·통근·보증금·면적·위험 근거를 보완해 주세요.",
                    why_needed="부분 순위나 비교 불가 결과를 최종 추천으로 노출하지 않습니다.",
                ),
            )
            return
        self._transition(stored, AgentState.VERIFICATION, "RANKING_COMPLETED")

    def _verify(self, stored: StoredAgentRun) -> None:
        eligibility = stored.run.eligibility
        ranking = stored.run.ranking
        context = stored.run.context
        gates: list[VerificationGate] = []
        document_ready = (
            context.document_id is None
            or context.use_manual_candidate_entry
            or (
                bool(context.resolved_document_values)
                and all(
                    value.source in {ValueSource.USER, ValueSource.DOCUMENT_CONFIRMED}
                    for value in context.resolved_document_values.values()
                )
            )
        )
        gates.append(
            VerificationGate(
                code="DOCUMENT_FIELDS_CONFIRMED",
                passed=document_ready,
                blocking_state=AgentState.CLARIFICATION if not document_ready else None,
                reason="문서 입력값은 사용자 확정 또는 수동 입력이어야 합니다.",
            )
        )
        document_values_match = self._document_values_match(context)
        gates.append(
            VerificationGate(
                code="USER_CONFIRMED_VALUES_APPLIED",
                passed=document_values_match,
                blocking_state=(
                    AgentState.CLARIFICATION if not document_values_match else None
                ),
                reason="사용자 확정·수정값은 해당 후보의 비용 및 순위 입력과 일치해야 합니다.",
            )
        )
        policy_grounded = bool(
            eligibility
            and eligibility.eligibility.document_version
            and eligibility.eligibility.rule_version
            and eligibility.grounded_conditions
            and all(item.citations for item in eligibility.grounded_conditions)
        )
        policy_reviewed = bool(
            policy_grounded and eligibility and eligibility.evidence_status is EvidenceStatus.EVIDENCE_FOUND
        )
        gates.append(
            VerificationGate(
                code="POLICY_VERSION_AND_EVIDENCE",
                passed=policy_reviewed,
                blocking_state=AgentState.OFFICIAL_CHECK if not policy_reviewed else None,
                reason="정책 규칙 버전과 독립 검토된 원문 인용이 필요합니다.",
            )
        )
        cost_units_valid = bool(stored.run.costs) and all(
            result.status is CalculationStatus.CALCULATED
            and all(
                scenario.breakdown.monthly_effective_cost.currency is Currency.KRW
                for scenario in result.scenarios
            )
            for result in stored.run.costs.values()
        )
        gates.append(
            VerificationGate(
                code="COST_UNITS_VALID",
                passed=cost_units_valid,
                blocking_state=AgentState.CLARIFICATION if not cost_units_valid else None,
                reason="모든 비용 결과가 계산 완료 상태이고 KRW 단위여야 합니다.",
            )
        )
        ranking_valid = bool(ranking and ranking.status is RankingStatus.RANKED)
        provenance_valid = ranking_valid and self._ranking_matches_costs(stored)
        gates.append(
            VerificationGate(
                code="RANKING_PROVENANCE_MATCH",
                passed=provenance_valid,
                blocking_state=AgentState.CLARIFICATION if not provenance_valid else None,
                reason="순위의 비용 버전·입력 해시는 실제 계산 결과와 일치해야 합니다.",
            )
        )
        contribution_valid = False
        if ranking is not None and ranking.status is RankingStatus.RANKED:
            contribution_valid = all(
                result.total_score is not None
                and abs(
                    sum((item.contribution for item in result.contributions), Decimal("0"))
                    - result.total_score
                )
                <= Decimal("0.02")
                for result in ranking.results
                if result.rank is not None
            )
        gates.append(
            VerificationGate(
                code="RANKING_EXPLANATION_MATCH",
                passed=contribution_valid,
                blocking_state=AgentState.CLARIFICATION if not contribution_valid else None,
                reason="순위 점수는 공개된 기준별 기여도의 합과 일치해야 합니다.",
            )
        )
        checklist = self._build_checklist(context)
        checklist_valid = bool(checklist) and all(
            item.version
            and item.citations
            and item.disclaimer
            and item.review_status is ReviewStatus.APPROVED
            and item.reviewer is not None
            and all(
                citation.review_status.value == "approved"
                and citation.retrieval_status.value == "retrieved"
                for citation in item.citations
            )
            for item in checklist
        )
        gates.append(
            VerificationGate(
                code="CHECKLIST_VERSIONED_AND_GROUNDED",
                passed=checklist_valid,
                blocking_state=AgentState.OFFICIAL_CHECK if not checklist_valid else None,
                reason="계약 전 체크리스트는 버전과 공식 출처를 가져야 합니다.",
            )
        )
        gates.append(
            VerificationGate(
                code="NO_GUARANTEE_LANGUAGE",
                passed=True,
                reason="최종 문구는 고정된 비보장 안내문만 사용합니다.",
            )
        )
        stored.run = stored.run.model_copy(update={"verification_gates": gates})
        clarification_failures = [
            gate.code
            for gate in gates
            if not gate.passed and gate.blocking_state is AgentState.CLARIFICATION
        ]
        if clarification_failures:
            self._pause(
                stored,
                AgentState.CLARIFICATION,
                AgentQuestion(
                    code=clarification_failures[0],
                    field="verification",
                    prompt="검증에 실패한 입력과 출처 버전을 확인해 주세요.",
                    why_needed="검증 실패 결과를 최종 추천으로 노출할 수 없습니다.",
                ),
            )
            return
        official_failures = [gate.code for gate in gates if not gate.passed]
        if official_failures:
            stored.run = stored.run.model_copy(
                update={
                    "official_check_reasons": official_failures,
                    "decision_card": self._decision_card(
                        stored, AgentState.OFFICIAL_CHECK, checklist
                    ),
                }
            )
            self._transition(stored, AgentState.OFFICIAL_CHECK, "OFFICIAL_REVIEW_REQUIRED")
            return
        stored.run = stored.run.model_copy(
            update={
                "decision_card": self._decision_card(stored, AgentState.DECISION_CARD, checklist)
            }
        )
        self._transition(stored, AgentState.DECISION_CARD, "ALL_VERIFICATION_GATES_PASSED")

    def _ranking_matches_costs(self, stored: StoredAgentRun) -> bool:
        ranking = stored.run.ranking
        eligibility = stored.run.eligibility
        if ranking is None or eligibility is None:
            return False
        for result in ranking.results:
            if result.rank is None:
                continue
            cost = stored.run.costs.get(result.candidate_id)
            if cost is None:
                return False
            provenance = result.provenance
            if (
                provenance.cost_calculation_version != cost.calculation_version
                or provenance.cost_input_sha256 != cost.input_sha256
            ):
                return False
            base_scenario = next(
                (scenario for scenario in cost.scenarios if scenario.scenario is ScenarioKind.BASE),
                None,
            )
            cost_contribution = next(
                (
                    item
                    for item in result.contributions
                    if item.criterion is Criterion.MONTHLY_EFFECTIVE_COST
                ),
                None,
            )
            if (
                base_scenario is None
                or cost_contribution is None
                or cost_contribution.raw_value
                != base_scenario.breakdown.monthly_effective_cost.amount
            ):
                return False
            policy_result = eligibility.eligibility
            if (
                policy_result.rule_version not in provenance.policy_versions
                or policy_result.status not in provenance.policy_statuses
            ):
                return False
        return True

    def _document_values_match(self, context: AgentContext) -> bool:
        if context.document_id is None or context.use_manual_candidate_entry:
            return True
        candidate_id = context.document_candidate_id
        if candidate_id is None:
            return False
        cost_request = next(
            (item for item in context.cost_requests if item.candidate_id == candidate_id),
            None,
        )
        ranking_request = context.ranking_request
        ranking_candidate = (
            None
            if ranking_request is None
            else next(
                (
                    item
                    for item in ranking_request.candidates
                    if item.candidate_id == candidate_id
                ),
                None,
            )
        )
        if cost_request is None or ranking_candidate is None:
            return False
        values = context.resolved_document_values
        comparisons: list[bool] = []
        try:
            if "deposit" in values:
                comparisons.append(
                    Decimal(values["deposit"].value) == cost_request.input.deposit.amount
                    and ranking_candidate.deposit == cost_request.input.deposit.amount
                )
            if "monthly_rent" in values:
                comparisons.append(
                    Decimal(values["monthly_rent"].value)
                    == cost_request.input.monthly_rent.amount
                )
            if "maintenance_fee" in values:
                maintenance = cost_request.input.monthly_maintenance
                comparisons.append(
                    maintenance is not None
                    and Decimal(values["maintenance_fee"].value) == maintenance.base.amount
                )
            if "area_sqm" in values:
                comparisons.append(
                    Decimal(values["area_sqm"].value) == ranking_candidate.area_sqm
                )
        except InvalidOperation:
            return False
        return bool(comparisons) and all(comparisons)

    def _build_checklist(self, context: AgentContext) -> list[ContractChecklistItem]:
        if context.policy_code is None or context.eligibility_input is None:
            return []
        policy = self._policies.get(context.policy_code)
        items: list[ContractChecklistItem] = []
        for item in policy.checklist:
            citations = self._evidence.citations_for_condition(
                policy.code, item.code, context.eligibility_input.as_of_date
            )
            if not citations:
                continue
            actor = "수탁은행·공식기관" if "BANK" in item.code else "사용자"
            items.append(
                ContractChecklistItem(
                    code=item.code,
                    version=policy.rule_version,
                    action=item.action,
                    applies_when=f"policy_code == {policy.code}",
                    verification_actor=actor,
                    review_status=policy.review.status,
                    reviewer=policy.review.reviewer,
                    citations=citations,
                )
            )
        return items

    def _decision_card(
        self,
        stored: StoredAgentRun,
        status: AgentState,
        checklist: list[ContractChecklistItem],
    ) -> DecisionCard:
        ranking = stored.run.ranking
        winners = [] if ranking is None else [
            result.candidate_id for result in ranking.results if result.rank == 1
        ]
        if status is AgentState.OFFICIAL_CHECK:
            summary = [
                "비용 계산과 후보 비교는 완료됐지만 정책 원문 또는 규칙 검토가 끝나지 않았습니다.",
                "아래 결과를 확정 추천으로 사용하지 말고 공식기관에서 조건을 다시 확인해 주세요.",
            ]
        else:
            summary = [
                "확인된 입력과 지정한 가중치에서 1위 후보를 산출했습니다.",
                "가중치와 상황이 바뀌면 결과도 달라질 수 있으므로 민감도 결과를 함께 확인해 주세요.",
            ]
        warnings = [] if ranking is None else ranking.warnings
        return DecisionCard(
            status=status,
            winner_candidate_ids=winners,
            summary_sentences=summary,
            warnings=warnings,
            checklist=checklist,
        )

    def _execute_tool(
        self,
        stored: StoredAgentRun,
        tool_name: str,
        payload: object,
        operation: Callable[[], T],
    ) -> T:
        input_hash = _payload_sha256(payload)
        cache_key = f"{tool_name}:{input_hash}"
        cached = stored.cache.get(cache_key)
        if cached is not None:
            usage = stored.run.usage
            stored.run = stored.run.model_copy(
                update={
                    "usage": usage.model_copy(update={"cache_hits": usage.cache_hits + 1})
                }
            )
            self._tool_record(
                stored,
                tool_name,
                ToolCallStatus.CACHED,
                input_hash,
                _payload_sha256(cached),
                0,
                0,
            )
            return cached  # type: ignore[return-value]
        last_error: Exception | None = None
        for attempt in range(1, stored.run.limits.max_retries_per_tool + 2):
            if stored.run.usage.tool_calls >= stored.run.limits.max_tool_calls:
                raise AgentLimitError("Maximum tool calls exceeded")
            usage = stored.run.usage
            stored.run = stored.run.model_copy(
                update={
                    "usage": usage.model_copy(
                        update={
                            "tool_calls": usage.tool_calls + 1,
                            "retries": usage.retries + int(attempt > 1),
                        }
                    )
                }
            )
            started = time.monotonic()
            try:
                result = operation()
                duration_ms = int((time.monotonic() - started) * 1000)
                if duration_ms > stored.run.limits.tool_timeout_seconds * 1000:
                    raise TimeoutError("Tool exceeded configured timeout")
                stored.cache[cache_key] = result
                self._tool_record(
                    stored,
                    tool_name,
                    ToolCallStatus.COMPLETED,
                    input_hash,
                    _payload_sha256(result),
                    attempt,
                    duration_ms,
                )
                return result
            except Exception as error:
                last_error = error
                duration_ms = int((time.monotonic() - started) * 1000)
                self._tool_record(
                    stored,
                    tool_name,
                    ToolCallStatus.FAILED,
                    input_hash,
                    None,
                    attempt,
                    duration_ms,
                    type(error).__name__,
                )
        assert last_error is not None
        raise last_error

    def _tool_record(
        self,
        stored: StoredAgentRun,
        tool_name: str,
        status: ToolCallStatus,
        input_sha256: str,
        output_sha256: str | None,
        attempt: int,
        duration_ms: int,
        error_code: str | None = None,
    ) -> None:
        calls = list(stored.run.tool_calls)
        calls.append(
            ToolCallRecord(
                sequence=len(calls) + 1,
                tool_name=tool_name,
                status=status,
                input_sha256=input_sha256,
                output_sha256=output_sha256,
                attempt=attempt,
                duration_ms=duration_ms,
                error_code=error_code,
                created_at=datetime.now(UTC),
            )
        )
        stored.run = stored.run.model_copy(
            update={"tool_calls": calls, "updated_at": datetime.now(UTC)}
        )

    def _record_manual_fallback(self, stored: StoredAgentRun, tool_name: str) -> None:
        self._tool_record(
            stored,
            tool_name,
            ToolCallStatus.MANUAL_FALLBACK,
            _payload_sha256({"state": stored.run.state}),
            None,
            0,
            0,
            "MANUAL_ENTRY_REQUIRED",
        )

    def _pause(
        self,
        stored: StoredAgentRun,
        state: AgentState,
        question: AgentQuestion,
    ) -> None:
        self._transition(stored, state, question.code)
        stored.run = stored.run.model_copy(update={"questions": [question]})

    def _official_check(self, stored: StoredAgentRun, reason: str) -> None:
        stored.run = stored.run.model_copy(update={"official_check_reasons": [reason]})
        self._transition(stored, AgentState.OFFICIAL_CHECK, reason)

    def _fail(self, stored: StoredAgentRun, reason: str) -> None:
        stored.run = stored.run.model_copy(update={"official_check_reasons": [reason]})
        self._transition(stored, AgentState.FAILED, reason)

    def _transition(
        self,
        stored: StoredAgentRun,
        new_state: AgentState,
        reason: str,
    ) -> None:
        old_state = stored.run.state
        if old_state is new_state and stored.run.transitions:
            return
        transitions = list(stored.run.transitions)
        transitions.append(
            StateTransition(
                sequence=len(transitions) + 1,
                from_state=old_state,
                to_state=new_state,
                reason_code=reason,
                created_at=datetime.now(UTC),
            )
        )
        stored.run = stored.run.model_copy(
            update={
                "state": new_state,
                "questions": [],
                "transitions": transitions,
                "updated_at": datetime.now(UTC),
            }
        )
