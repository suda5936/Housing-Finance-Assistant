from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from homefit_api.main import app
from homefit_api.ranking import (
    CandidateDisposition,
    Criterion,
    RankingRequest,
    RankingStatus,
    RankingWeights,
    rank_candidates,
)

HASH = "b" * 64
REFERENCE = datetime(2026, 7, 24, tzinfo=UTC)


def _candidate(
    candidate_id: str,
    *,
    cost: str,
    commute: str,
    deposit: str,
    area: str,
    risk: str,
    **overrides: object,
) -> dict[str, object]:
    candidate: dict[str, object] = {
        "candidate_id": candidate_id,
        "label": f"후보 {candidate_id}",
        "district": "마포구",
        "monthly_effective_cost": cost,
        "cost_scenario": "base",
        "cost_calculation_version": "cost-v1",
        "cost_input_sha256": HASH,
        "commute_minutes": commute,
        "commute_reference_at": REFERENCE.isoformat(),
        "deposit": deposit,
        "area_sqm": area,
        "risk_score": risk,
        "risk_basis": "사용자 확인 위험신호를 0~100으로 환산",
        "policy_statuses": ["OFFICIAL_CHECK_NEEDED"],
        "policy_versions": ["policy-draft-v1"],
    }
    candidate.update(overrides)
    return candidate


def _payload() -> dict[str, object]:
    return {
        "candidates": [
            _candidate("A", cost="600000", commute="60", deposit="10000000", area="30", risk="20"),
            _candidate("B", cost="700000", commute="20", deposit="20000000", area="40", risk="10"),
            _candidate("C", cost="800000", commute="40", deposit="5000000", area="25", risk="30"),
        ]
    }


def _request(payload: dict[str, object] | None = None) -> RankingRequest:
    return RankingRequest.model_validate(payload or _payload())


def test_default_weight_ranking_matches_hand_calculation() -> None:
    result = rank_candidates(_request())

    assert result.status is RankingStatus.RANKED
    assert [item.candidate_id for item in result.results] == ["B", "A", "C"]
    assert [item.rank for item in result.results] == [1, 2, 3]
    assert result.results[0].total_score == Decimal("65.00")
    assert result.results[1].total_score == Decimal("58.33")
    assert result.results[2].total_score == Decimal("27.50")
    assert all(
        sum((part.contribution for part in item.contributions), Decimal("0"))
        == item.total_score
        for item in result.results
    )


def test_same_input_produces_same_ranking_and_hash() -> None:
    first = rank_candidates(_request())
    second = rank_candidates(_request())

    assert first == second
    assert first.input_sha256 == second.input_sha256


def test_missing_weighted_criterion_is_not_forced_into_ranking() -> None:
    payload = _payload()
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    candidates[0]["risk_score"] = None
    candidates[0]["risk_basis"] = None

    result = rank_candidates(_request(payload))
    missing = next(item for item in result.results if item.candidate_id == "A")

    assert result.status is RankingStatus.PARTIAL
    assert missing.disposition is CandidateDisposition.NOT_COMPARABLE
    assert missing.rank is None
    assert missing.missing_fields == ["risk_score"]


def test_hard_constraint_failure_is_separate_from_missing_data() -> None:
    payload = _payload()
    payload["hard_constraints"] = {"max_deposit": "15000000"}

    result = rank_candidates(_request(payload))
    failed = next(item for item in result.results if item.candidate_id == "B")

    assert result.status is RankingStatus.PARTIAL
    assert failed.disposition is CandidateDisposition.HARD_CONSTRAINT_FAILED
    assert failed.missing_fields == []
    assert "HARD_DEPOSIT_MAX_FAILED" in failed.hard_constraint_failures


def test_all_candidates_missing_required_data_returns_not_comparable() -> None:
    payload = _payload()
    for candidate in payload["candidates"]:  # type: ignore[union-attr]
        candidate["monthly_effective_cost"] = None
        candidate["cost_calculation_version"] = None
        candidate["cost_input_sha256"] = None

    result = rank_candidates(_request(payload))

    assert result.status is RankingStatus.NOT_COMPARABLE
    assert all(item.rank is None for item in result.results)
    assert result.sensitivity is None


def test_identical_candidates_receive_same_dense_rank() -> None:
    first = _candidate("A", cost="600000", commute="30", deposit="10000000", area="30", risk="20")
    second = deepcopy(first)
    second["candidate_id"] = "B"
    second["label"] = "후보 B"

    result = rank_candidates(_request({"candidates": [first, second]}))

    assert [item.rank for item in result.results] == [1, 1]
    assert [item.total_score for item in result.results] == [Decimal("50.00"), Decimal("50.00")]


def test_pareto_dominated_candidate_is_flagged() -> None:
    better = _candidate("A", cost="500000", commute="20", deposit="5000000", area="40", risk="10")
    worse = _candidate("B", cost="600000", commute="30", deposit="6000000", area="30", risk="20")

    result = rank_candidates(_request({"candidates": [better, worse]}))
    dominated = next(item for item in result.results if item.candidate_id == "B")

    assert dominated.dominated_by == ["A"]
    assert "PARETO_DOMINATED" in dominated.reason_codes


def test_sensitivity_reports_numeric_weight_change_and_winner_flip() -> None:
    result = rank_candidates(_request())

    assert result.sensitivity is not None
    assert result.sensitivity.winner_changes is True
    cost_plus_ten = next(
        scenario
        for scenario in result.sensitivity.scenarios
        if scenario.changed_criterion is Criterion.MONTHLY_EFFECTIVE_COST
        and scenario.delta_points == Decimal("10")
    )
    assert cost_plus_ten.weights.monthly_effective_cost == Decimal("50")
    assert cost_plus_ten.winner_ids == ["A"]


def test_sensitivity_redistribution_respects_weight_cap() -> None:
    payload = _payload()
    payload["weights"] = {
        "monthly_effective_cost": "60",
        "commute_minutes": "15",
        "deposit": "10",
        "area_sqm": "5",
        "risk_score": "10",
        "infrastructure_score": "0",
    }

    result = rank_candidates(_request(payload))

    assert result.sensitivity is not None
    assert all(
        all(weight <= 60 for weight in scenario.weights.as_dict().values())
        for scenario in result.sensitivity.scenarios
    )


def test_infrastructure_requires_source_and_is_exposed_in_provenance() -> None:
    payload = _payload()
    payload["weights"] = {
        "monthly_effective_cost": "35",
        "commute_minutes": "20",
        "deposit": "15",
        "area_sqm": "10",
        "risk_score": "10",
        "infrastructure_score": "10",
    }
    for index, candidate in enumerate(payload["candidates"]):  # type: ignore[union-attr]
        candidate["infrastructure_score"] = str(60 + index * 10)
        candidate["infrastructure_evidence"] = {
            "source": "user",
            "methodology": "사용자가 교통·상점 접근성을 0~100으로 평가",
            "reference_at": REFERENCE.isoformat(),
            "source_reference": "사용자 입력",
        }

    result = rank_candidates(_request(payload))

    assert result.status is RankingStatus.RANKED
    assert all(item.provenance.infrastructure_evidence is not None for item in result.results)
    assert all(
        Criterion.INFRASTRUCTURE_SCORE in [part.criterion for part in item.contributions]
        for item in result.results
    )


def test_reference_time_mismatch_and_mixed_calculation_versions_warn() -> None:
    payload = _payload()
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    candidates[1]["commute_reference_at"] = (REFERENCE - timedelta(days=31)).isoformat()
    candidates[1]["cost_calculation_version"] = "cost-v2"

    result = rank_candidates(_request(payload))

    assert "COMMUTE_REFERENCE_TIME_MISMATCH_OVER_30_DAYS" in result.warnings
    assert "MIXED_COST_CALCULATION_VERSIONS" in result.warnings


@pytest.mark.parametrize(
    "weights",
    [
        {"monthly_effective_cost": 40, "commute_minutes": 25, "deposit": 15, "area_sqm": 10, "risk_score": 9, "infrastructure_score": 0},
        {"monthly_effective_cost": 61, "commute_minutes": 19, "deposit": 10, "area_sqm": 5, "risk_score": 5, "infrastructure_score": 0},
    ],
)
def test_invalid_or_dominating_weights_are_rejected(weights: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        RankingWeights.model_validate(weights)


def test_mixed_cost_scenarios_are_rejected() -> None:
    payload = _payload()
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    candidates[1]["cost_scenario"] = "optimistic"

    with pytest.raises(ValidationError):
        _request(payload)


def test_ranking_api_returns_structured_result_and_validation_error() -> None:
    client = TestClient(app)

    response = client.post("/rankings/compare", json=_payload())
    invalid = client.post("/rankings/compare", json={"candidates": [_payload()["candidates"][0]]})  # type: ignore[index]

    assert response.status_code == 200
    assert response.json()["ranking_version"] == "ranking-v1"
    assert response.json()["results"][0]["candidate_id"] == "B"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
