from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from homefit_api.costs import CostCalculationInput, ScenarioKind, calculate_costs
from homefit_api.data import (
    ConsentInput,
    HousingCandidateInput,
    InMemoryDataRepository,
    Money,
    UserProfileInput,
)
from homefit_api.documents import (
    DocumentService,
    ExtractionOutput,
    ExtractionUnavailableError,
)
from homefit_api.main import app
from homefit_api.orchestration import (
    AgentAdvanceRequest,
    AgentContext,
    AgentContextPatch,
    AgentLimits,
    AgentOrchestrator,
    AgentRunCreate,
    AgentState,
    CandidateCostRequest,
    ResolvedValue,
    ToolCallStatus,
    ValueSource,
    merge_resolved_values,
)
from homefit_api.policies import (
    EligibilityInput,
    EligibilityStatus,
    PolicyCatalog,
    PolicyRegistry,
)
from homefit_api.rag import PolicyEvidenceRegistry, PolicySourceCatalog
from homefit_api.ranking import RankingRequest
from homefit_api.settings import Settings


class UnavailableExtractor:
    def extract(self, path: Path, media_type: str) -> ExtractionOutput:
        del path, media_type
        raise ExtractionUnavailableError("OCR executable missing")


def _png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (100).to_bytes(4, "big")
        + (100).to_bytes(4, "big")
        + b"synthetic-image-data"
    )


def _repository(tmp_path: Path):
    repository = InMemoryDataRepository(upload_dir=tmp_path)
    created = repository.create_session(
        ConsentInput(
            consent_version="privacy-v1",
            privacy_notice_accepted=True,
            sensitive_data_notice_accepted=True,
        )
    )
    documents = DocumentService(Settings(upload_dir=str(tmp_path)))
    return repository, created, documents


def _add_profile_and_candidates(repository: InMemoryDataRepository, created: object):
    session_id = created.session.id  # type: ignore[attr-defined]
    token = created.access_token  # type: ignore[attr-defined]
    repository.save_profile(
        session_id,
        token,
        UserProfileInput(
            age_years=25,
            monthly_net_income=Money(amount=Decimal("3000000")),
            liquid_assets=Money(amount=Decimal("20000000")),
            available_deposit=Money(amount=Decimal("10000000")),
            household_type="single",
            is_homeless=True,
            workplace_district="중구",
        ),
    )
    candidates = []
    for index, values in enumerate(
        [
            ("후보 A", "마포구", "10000000", "550000", "35", 35),
            ("후보 B", "성동구", "15000000", "600000", "40", 25),
        ]
    ):
        label, district, deposit, rent, area, commute = values
        candidates.append(
            repository.add_candidate(
                session_id,
                token,
                HousingCandidateInput(
                    label=label,
                    district=district,
                    deposit=Money(amount=Decimal(deposit)),
                    monthly_rent=Money(amount=Decimal(rent)),
                    monthly_maintenance=Money(amount=Decimal("70000")),
                    area_sqm=Decimal(area),
                    contract_months=12,
                    commute_minutes_one_way=commute,
                    monthly_commute_cost=Money(amount=Decimal("62000") + index * 1000),
                ),
            )
        )
    return candidates


def _cost_input(candidate: object) -> CostCalculationInput:
    return CostCalculationInput.model_validate(
        {
            "candidate_label": candidate.label,
            "candidate_district": candidate.district,
            "monthly_net_income": {"amount": "3000000", "currency": "KRW"},
            "deposit": candidate.deposit.model_dump(mode="json"),
            "own_funds_for_deposit": {"amount": "5000000", "currency": "KRW"},
            "monthly_rent": candidate.monthly_rent.model_dump(mode="json"),
            "monthly_maintenance": {
                "minimum": {"amount": "60000", "currency": "KRW"},
                "base": {"amount": "70000", "currency": "KRW"},
                "maximum": {"amount": "90000", "currency": "KRW"},
            },
            "annual_borrowing_rate": {"minimum": "3", "base": "4", "maximum": "5"},
            "contract_months": 12,
            "initial_costs": {"amount": "1200000", "currency": "KRW"},
            "commute": {
                "source": "manual",
                "transport_mode": "public_transit",
                "reference_at": datetime(2026, 7, 24, tzinfo=UTC).isoformat(),
                "commute_minutes_one_way": candidate.commute_minutes_one_way,
                "monthly_cost": candidate.monthly_commute_cost.model_dump(mode="json"),
            },
        }
    )


def _eligibility() -> EligibilityInput:
    return EligibilityInput(
        as_of_date=date(2026, 5, 10),
        age_years=25,
        region="서울특별시",
        median_income_ratio_percent="100",
        is_homeless=True,
        deposit="10000000",
        monthly_rent="500000",
        received_same_seoul_support_before=False,
        receiving_other_monthly_rent_support=False,
    )


def _full_context(candidates: list[object], *, cost_delta: Decimal = Decimal("0")) -> AgentContext:
    costs = [
        CandidateCostRequest(candidate_id=str(candidate.id), input=_cost_input(candidate))
        for candidate in candidates
    ]
    calculated = {
        item.candidate_id: calculate_costs(item.input)
        for item in costs
    }
    ranking_candidates = []
    for index, candidate in enumerate(candidates):
        result = calculated[str(candidate.id)]
        base = next(item for item in result.scenarios if item.scenario is ScenarioKind.BASE)
        ranking_candidates.append(
            {
                "candidate_id": str(candidate.id),
                "label": candidate.label,
                "district": candidate.district,
                "monthly_effective_cost": str(
                    base.breakdown.monthly_effective_cost.amount + cost_delta
                ),
                "cost_scenario": "base",
                "cost_calculation_version": result.calculation_version,
                "cost_input_sha256": result.input_sha256,
                "commute_minutes": str(candidate.commute_minutes_one_way),
                "commute_reference_at": datetime(2026, 7, 24, tzinfo=UTC).isoformat(),
                "deposit": str(candidate.deposit.amount),
                "area_sqm": str(candidate.area_sqm),
                "risk_score": str(10 + index * 10),
                "risk_basis": "사용자가 확인한 위험신호를 점수화",
                "policy_statuses": ["OFFICIAL_CHECK_NEEDED"],
                "policy_versions": ["seoul-rent-2026-draft-v1"],
            }
        )
    return AgentContext(
        policy_code="seoul_youth_monthly_rent_2026",
        eligibility_input=_eligibility(),
        cost_requests=costs,
        ranking_request=RankingRequest.model_validate({"candidates": ranking_candidates}),
    )


def _approved_registries() -> tuple[PolicyRegistry, PolicyEvidenceRegistry]:
    policy_data = PolicyRegistry.load_default().catalog.model_dump(mode="json")
    policy = policy_data["policies"][0]
    policy["source"]["original_sha256"] = "a" * 64
    policy["review"] = {
        "status": "approved",
        "author": "rule-author",
        "reviewer": "independent-reviewer",
        "reviewed_at": datetime(2026, 7, 24, tzinfo=UTC).isoformat(),
    }
    policies = PolicyRegistry(PolicyCatalog.model_validate(policy_data))

    evidence_data = PolicyEvidenceRegistry.load_default().catalog.model_dump(mode="json")
    source = evidence_data["documents"][0]
    source["review_status"] = "approved"
    source["reviewer"] = "independent-reviewer"
    source["reviewed_at"] = datetime(2026, 7, 24, tzinfo=UTC).isoformat()
    evidence = PolicyEvidenceRegistry(PolicySourceCatalog.model_validate(evidence_data))
    return policies, evidence


def test_run_pauses_at_profile_then_candidate_and_prioritized_question(tmp_path: Path) -> None:
    repository, created, documents = _repository(tmp_path)
    orchestrator = AgentOrchestrator(PolicyRegistry.load_default(), PolicyEvidenceRegistry.load_default())

    run = orchestrator.create_run(
        repository, documents, created.session.id, created.access_token, AgentRunCreate()
    )

    assert run.state is AgentState.PROFILE
    assert run.questions[0].code == "PROFILE_REQUIRED"

    repository.save_profile(
        created.session.id,
        created.access_token,
        UserProfileInput(
            age_years=25,
            monthly_net_income=Money(amount=Decimal("3000000")),
            liquid_assets=Money(amount=Decimal("20000000")),
            available_deposit=Money(amount=Decimal("10000000")),
            household_type="single",
            is_homeless=True,
        ),
    )
    run = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentAdvanceRequest(),
    )
    assert run.state is AgentState.CANDIDATE_INPUT
    assert run.questions[0].code == "TWO_CANDIDATES_REQUIRED"


def test_full_default_flow_stops_at_official_check_and_is_resumable(tmp_path: Path) -> None:
    repository, created, documents = _repository(tmp_path)
    candidates = _add_profile_and_candidates(repository, created)
    orchestrator = AgentOrchestrator(PolicyRegistry.load_default(), PolicyEvidenceRegistry.load_default())
    run = orchestrator.create_run(
        repository,
        documents,
        created.session.id,
        created.access_token,
        AgentRunCreate(context=_full_context(candidates)),
    )

    finished = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentAdvanceRequest(),
    )
    resumed = orchestrator.get_run(
        repository, created.session.id, created.access_token, run.id
    )

    assert finished.state is AgentState.OFFICIAL_CHECK
    assert resumed == finished
    assert finished.decision_card is not None
    assert finished.decision_card.status is AgentState.OFFICIAL_CHECK
    assert finished.decision_card.checklist
    assert all(item.version and item.citations for item in finished.decision_card.checklist)
    assert finished.usage.llm_calls == 0
    assert finished.usage.estimated_cost_krw == 0


def test_independently_approved_rules_and_sources_can_reach_decision_card(tmp_path: Path) -> None:
    repository, created, documents = _repository(tmp_path)
    candidates = _add_profile_and_candidates(repository, created)
    policies, evidence = _approved_registries()
    context = _full_context(candidates)
    ranking = context.ranking_request
    assert ranking is not None
    approved_candidates = [
        candidate.model_copy(
            update={
                "policy_statuses": [EligibilityStatus.ELIGIBLE],
                "policy_versions": ["seoul-rent-2026-draft-v1"],
            }
        )
        for candidate in ranking.candidates
    ]
    context = context.model_copy(
        update={"ranking_request": ranking.model_copy(update={"candidates": approved_candidates})}
    )
    orchestrator = AgentOrchestrator(policies, evidence)
    run = orchestrator.create_run(
        repository,
        documents,
        created.session.id,
        created.access_token,
        AgentRunCreate(context=context),
    )

    finished = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentAdvanceRequest(),
    )

    assert finished.state is AgentState.DECISION_CARD
    assert all(gate.passed for gate in finished.verification_gates)
    assert finished.decision_card is not None
    assert finished.decision_card.winner_candidate_ids


def test_user_correction_always_beats_later_document_value() -> None:
    now = datetime.now(UTC)
    current = {
        "deposit": ResolvedValue(value="10000000", source=ValueSource.USER, updated_at=now)
    }

    merged = merge_resolved_values(
        current,
        {"deposit": "20000000", "monthly_rent": "500000"},
        ValueSource.DOCUMENT_CONFIRMED,
    )

    assert merged["deposit"].value == "10000000"
    assert merged["deposit"].source is ValueSource.USER
    assert merged["monthly_rent"].source is ValueSource.DOCUMENT_CONFIRMED


def test_ocr_failure_pauses_on_manual_entry_path(tmp_path: Path) -> None:
    repository, created, _ = _repository(tmp_path)
    candidates = _add_profile_and_candidates(repository, created)
    documents = DocumentService(
        Settings(upload_dir=str(tmp_path)), extractor=UnavailableExtractor()
    )
    uploaded = documents.upload(
        repository,
        created.session.id,
        created.access_token,
        filename="lease.png",
        declared_media_type="image/png",
        content=_png(),
    )
    context = _full_context(candidates).model_copy(update={"document_id": uploaded.id})
    orchestrator = AgentOrchestrator(PolicyRegistry.load_default(), PolicyEvidenceRegistry.load_default())
    run = orchestrator.create_run(
        repository,
        documents,
        created.session.id,
        created.access_token,
        AgentRunCreate(context=context),
    )

    paused = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentAdvanceRequest(),
    )

    assert paused.state is AgentState.CLARIFICATION
    assert paused.questions[0].code == "DOCUMENT_MANUAL_ENTRY_REQUIRED"
    assert any(call.status is ToolCallStatus.MANUAL_FALLBACK for call in paused.tool_calls)


def test_same_scenario_uses_tool_cache_after_scenario_update(tmp_path: Path) -> None:
    repository, created, documents = _repository(tmp_path)
    candidates = _add_profile_and_candidates(repository, created)
    orchestrator = AgentOrchestrator(PolicyRegistry.load_default(), PolicyEvidenceRegistry.load_default())
    run = orchestrator.create_run(
        repository,
        documents,
        created.session.id,
        created.access_token,
        AgentRunCreate(context=_full_context(candidates)),
    )
    first = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentAdvanceRequest(),
    )
    updated = orchestrator.update_context(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentContextPatch(scenario_update=True),
    )
    second = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        updated.id,
        AgentAdvanceRequest(),
    )

    assert first.state is AgentState.OFFICIAL_CHECK
    assert second.context.scenario_revision == 1
    assert second.usage.cache_hits >= 4


def test_final_run_rejects_stale_context_change_without_scenario_update(tmp_path: Path) -> None:
    repository, created, documents = _repository(tmp_path)
    candidates = _add_profile_and_candidates(repository, created)
    orchestrator = AgentOrchestrator(PolicyRegistry.load_default(), PolicyEvidenceRegistry.load_default())
    run = orchestrator.create_run(
        repository,
        documents,
        created.session.id,
        created.access_token,
        AgentRunCreate(context=_full_context(candidates)),
    )
    finished = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentAdvanceRequest(),
    )

    with pytest.raises(ValueError, match="scenario_update"):
        orchestrator.update_context(
            repository,
            documents,
            created.session.id,
            created.access_token,
            finished.id,
            AgentContextPatch(policy_code="housing_stability_monthly_loan"),
        )


def test_ranking_cost_tampering_is_blocked_by_verification(tmp_path: Path) -> None:
    repository, created, documents = _repository(tmp_path)
    candidates = _add_profile_and_candidates(repository, created)
    orchestrator = AgentOrchestrator(PolicyRegistry.load_default(), PolicyEvidenceRegistry.load_default())
    run = orchestrator.create_run(
        repository,
        documents,
        created.session.id,
        created.access_token,
        AgentRunCreate(context=_full_context(candidates, cost_delta=Decimal("1"))),
    )

    result = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentAdvanceRequest(),
    )

    assert result.state is AgentState.CLARIFICATION
    assert result.questions[0].code == "RANKING_PROVENANCE_MATCH"
    assert result.decision_card is None


def test_incomplete_cost_result_returns_to_clarification(tmp_path: Path) -> None:
    repository, created, documents = _repository(tmp_path)
    candidates = _add_profile_and_candidates(repository, created)
    context = _full_context(candidates)
    first_cost = context.cost_requests[0]
    incomplete = first_cost.model_copy(
        update={"input": first_cost.input.model_copy(update={"monthly_maintenance": None})}
    )
    context = context.model_copy(
        update={"cost_requests": [incomplete, *context.cost_requests[1:]]}
    )
    orchestrator = AgentOrchestrator(PolicyRegistry.load_default(), PolicyEvidenceRegistry.load_default())
    run = orchestrator.create_run(
        repository,
        documents,
        created.session.id,
        created.access_token,
        AgentRunCreate(context=context),
    )

    result = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentAdvanceRequest(),
    )

    assert result.state is AgentState.CLARIFICATION
    assert result.questions[0].code == "COST_RESULT_INCOMPLETE"
    assert result.ranking is None


def test_partial_ranking_returns_to_clarification(tmp_path: Path) -> None:
    repository, created, documents = _repository(tmp_path)
    candidates = _add_profile_and_candidates(repository, created)
    context = _full_context(candidates)
    ranking = context.ranking_request
    assert ranking is not None
    first = ranking.candidates[0].model_copy(
        update={"risk_score": None, "risk_basis": None}
    )
    context = context.model_copy(
        update={
            "ranking_request": ranking.model_copy(
                update={"candidates": [first, *ranking.candidates[1:]]}
            )
        }
    )
    orchestrator = AgentOrchestrator(PolicyRegistry.load_default(), PolicyEvidenceRegistry.load_default())
    run = orchestrator.create_run(
        repository,
        documents,
        created.session.id,
        created.access_token,
        AgentRunCreate(context=context),
    )

    result = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentAdvanceRequest(),
    )

    assert result.state is AgentState.CLARIFICATION
    assert result.questions[0].code == "RANKING_NOT_COMPLETE"
    assert result.decision_card is None


def test_step_limit_fails_closed_without_decision(tmp_path: Path) -> None:
    repository, created, documents = _repository(tmp_path)
    candidates = _add_profile_and_candidates(repository, created)
    orchestrator = AgentOrchestrator(PolicyRegistry.load_default(), PolicyEvidenceRegistry.load_default())
    run = orchestrator.create_run(
        repository,
        documents,
        created.session.id,
        created.access_token,
        AgentRunCreate(
            context=_full_context(candidates),
            limits=AgentLimits(max_steps=1),
        ),
    )

    result = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentAdvanceRequest(),
    )

    assert result.state is AgentState.FAILED
    assert result.decision_card is None
    assert result.official_check_reasons == ["MAX_STEPS_EXCEEDED"]


def test_session_deletion_removes_agent_run_cache(tmp_path: Path) -> None:
    repository, created, documents = _repository(tmp_path)
    orchestrator = AgentOrchestrator(PolicyRegistry.load_default(), PolicyEvidenceRegistry.load_default())
    run = orchestrator.create_run(
        repository, documents, created.session.id, created.access_token, AgentRunCreate()
    )

    receipt = repository.delete_session(created.session.id, created.access_token)

    assert receipt.cache_entries_deleted == 1
    assert orchestrator.delete_session_runs(run.session_id) == 0


def test_unknown_policy_fails_to_official_check_not_generated_answer(tmp_path: Path) -> None:
    repository, created, documents = _repository(tmp_path)
    candidates = _add_profile_and_candidates(repository, created)
    context = _full_context(candidates).model_copy(update={"policy_code": "unknown_policy"})
    orchestrator = AgentOrchestrator(PolicyRegistry.load_default(), PolicyEvidenceRegistry.load_default())
    run = orchestrator.create_run(
        repository,
        documents,
        created.session.id,
        created.access_token,
        AgentRunCreate(context=context),
    )

    result = orchestrator.advance(
        repository,
        documents,
        created.session.id,
        created.access_token,
        run.id,
        AgentAdvanceRequest(),
    )

    assert result.state is AgentState.OFFICIAL_CHECK
    assert result.official_check_reasons == ["POLICY_TOOL_FAILED"]
    assert result.decision_card is None


def test_agent_api_creates_resumes_and_protects_run() -> None:
    client = TestClient(app)
    session = client.post(
        "/sessions",
        json={
            "consent_version": "privacy-v1",
            "privacy_notice_accepted": True,
            "sensitive_data_notice_accepted": True,
        },
    ).json()
    session_id = session["session"]["id"]
    token = session["access_token"]

    created = client.post(
        f"/sessions/{session_id}/agent-runs",
        headers={"X-Session-Token": token},
        json={},
    )
    run_id = created.json()["id"]
    resumed = client.get(
        f"/sessions/{session_id}/agent-runs/{run_id}",
        headers={"X-Session-Token": token},
    )
    forbidden = client.get(
        f"/sessions/{session_id}/agent-runs/{run_id}",
        headers={"X-Session-Token": "x" * 32},
    )

    assert created.status_code == 201
    assert created.json()["state"] == "profile"
    assert resumed.status_code == 200
    assert resumed.json()["revision"] == 1
    assert forbidden.status_code == 403
