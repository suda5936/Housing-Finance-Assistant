from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from homefit_api.costs import (
    CalculationStatus,
    CostCalculationInput,
    ScenarioKind,
    calculate_costs,
)
from homefit_api.main import app


def _payload() -> dict[str, object]:
    return {
        "candidate_label": "A주택",
        "candidate_district": "서울 마포구",
        "monthly_net_income": {"amount": "2500000", "currency": "KRW"},
        "deposit": {"amount": "10000000", "currency": "KRW"},
        "own_funds_for_deposit": {"amount": "4000000", "currency": "KRW"},
        "monthly_rent": {"amount": "550000", "currency": "KRW"},
        "monthly_maintenance": {
            "minimum": {"amount": "70000", "currency": "KRW"},
            "base": {"amount": "70000", "currency": "KRW"},
            "maximum": {"amount": "70000", "currency": "KRW"},
        },
        "annual_borrowing_rate": {"minimum": "4", "base": "4", "maximum": "4"},
        "annual_own_funds_opportunity_rate": {
            "minimum": "2",
            "base": "2",
            "maximum": "2",
        },
        "contract_months": 12,
        "initial_costs": {"amount": "1200000", "currency": "KRW"},
        "commute": {
            "source": "manual",
            "transport_mode": "public_transit",
            "reference_at": datetime(2026, 7, 24, tzinfo=UTC).isoformat(),
            "commute_minutes_one_way": 35,
            "monthly_cost": {"amount": "62000", "currency": "KRW"},
        },
        "monthly_living_cost": {"amount": "0", "currency": "KRW"},
        "supports": [
            {
                "name": "확정 월세 지원",
                "monthly_amount": {"amount": "200000", "currency": "KRW"},
                "start_month": 1,
                "duration_months": 12,
                "status": "confirmed",
                "source_version": "synthetic-v1",
            }
        ],
    }


def _request(overrides: dict[str, object] | None = None) -> CostCalculationInput:
    payload = _payload()
    if overrides:
        payload.update(overrides)
    return CostCalculationInput.model_validate(payload)


def _scenario(result: object, kind: ScenarioKind):
    scenarios = result.scenarios  # type: ignore[attr-defined]
    return next(item for item in scenarios if item.scenario is kind)


def test_manual_calculation_matches_hand_calculated_sample() -> None:
    result = calculate_costs(_request())
    repeated = calculate_costs(_request())
    base = _scenario(result, ScenarioKind.BASE)

    assert result.status is CalculationStatus.CALCULATED
    assert result.input_sha256 == repeated.input_sha256
    assert result == repeated
    assert base.breakdown.monthly_borrowing_cost.amount == Decimal("20000")
    assert base.breakdown.monthly_opportunity_cost.amount == Decimal("6667")
    assert base.breakdown.monthly_initial_cost_amortization.amount == Decimal("100000")
    assert base.breakdown.monthly_support_average.amount == Decimal("200000")
    assert base.breakdown.monthly_effective_cost.amount == Decimal("608667")
    assert base.breakdown.contract_total_cost.amount == Decimal("7304000")
    assert base.breakdown.housing_cost_burden_percent == Decimal("24.35")


def test_scenarios_apply_range_and_expected_support_rules() -> None:
    payload = _payload()
    payload["monthly_maintenance"] = {
        "minimum": {"amount": "50000", "currency": "KRW"},
        "base": {"amount": "70000", "currency": "KRW"},
        "maximum": {"amount": "100000", "currency": "KRW"},
    }
    payload["annual_borrowing_rate"] = {"minimum": "3", "base": "4", "maximum": "5"}
    payload["supports"] = [
        {
            "name": "선정 전 지원",
            "monthly_amount": {"amount": "100000", "currency": "KRW"},
            "start_month": 1,
            "duration_months": 12,
            "status": "expected",
            "source_version": "synthetic-v1",
        }
    ]

    result = calculate_costs(CostCalculationInput.model_validate(payload))
    optimistic = _scenario(result, ScenarioKind.OPTIMISTIC)
    base = _scenario(result, ScenarioKind.BASE)
    conservative = _scenario(result, ScenarioKind.CONSERVATIVE)

    assert optimistic.breakdown.contract_total_cost.amount < base.breakdown.contract_total_cost.amount
    assert base.breakdown.contract_total_cost.amount < conservative.breakdown.contract_total_cost.amount
    assert "EXPECTED_SUPPORT_INCLUDED" in optimistic.reason_codes
    assert "EXPECTED_SUPPORT_EXCLUDED" in base.reason_codes
    assert "EXPECTED_SUPPORT_EXCLUDED" in conservative.reason_codes


@pytest.mark.parametrize(
    ("missing_key", "expected_field"),
    [
        ("monthly_maintenance", "monthly_maintenance"),
        ("commute", "commute"),
    ],
)
def test_missing_information_returns_hold_state(
    missing_key: str, expected_field: str
) -> None:
    payload = _payload()
    payload[missing_key] = None

    result = calculate_costs(CostCalculationInput.model_validate(payload))

    assert result.status is CalculationStatus.MISSING_INFORMATION
    assert result.missing_fields == [expected_field]
    assert result.scenarios == []


def test_round_trip_commute_cost_uses_workdays() -> None:
    commute = deepcopy(_payload()["commute"])
    assert isinstance(commute, dict)
    commute.pop("monthly_cost")
    commute["round_trip_cost"] = {"amount": "3000", "currency": "KRW"}
    commute["workdays_per_month"] = 20

    result = calculate_costs(_request({"commute": commute}))
    base = _scenario(result, ScenarioKind.BASE)

    assert base.breakdown.monthly_commute_cost.amount == Decimal("60000")
    assert base.assumptions.workdays_per_month == 20


def test_rent_change_recalculates_contract_delta() -> None:
    before = calculate_costs(_request())
    after = calculate_costs(
        _request({"monthly_rent": {"amount": "600000", "currency": "KRW"}})
    )

    before_total = _scenario(before, ScenarioKind.BASE).breakdown.contract_total_cost.amount
    after_total = _scenario(after, ScenarioKind.BASE).breakdown.contract_total_cost.amount

    assert after_total - before_total == Decimal("600000")


def test_refundable_deposit_principal_is_not_counted_when_rates_are_zero() -> None:
    zero_rates = {"minimum": "0", "base": "0", "maximum": "0"}
    low_deposit = calculate_costs(
        _request(
            {
                "deposit": {"amount": "5000000", "currency": "KRW"},
                "own_funds_for_deposit": {"amount": "5000000", "currency": "KRW"},
                "annual_borrowing_rate": zero_rates,
                "annual_own_funds_opportunity_rate": zero_rates,
            }
        )
    )
    high_deposit = calculate_costs(
        _request(
            {
                "deposit": {"amount": "20000000", "currency": "KRW"},
                "own_funds_for_deposit": {"amount": "20000000", "currency": "KRW"},
                "annual_borrowing_rate": zero_rates,
                "annual_own_funds_opportunity_rate": zero_rates,
            }
        )
    )

    assert (
        _scenario(low_deposit, ScenarioKind.BASE).breakdown.contract_total_cost
        == _scenario(high_deposit, ScenarioKind.BASE).breakdown.contract_total_cost
    )


def test_support_period_is_clipped_to_contract() -> None:
    payload = _payload()
    payload["contract_months"] = 6
    payload["supports"] = [
        {
            "name": "3개월차부터 지원",
            "monthly_amount": {"amount": "200000", "currency": "KRW"},
            "start_month": 3,
            "duration_months": 12,
            "status": "confirmed",
            "source_version": "synthetic-v1",
        }
    ]

    result = calculate_costs(CostCalculationInput.model_validate(payload))
    base = _scenario(result, ScenarioKind.BASE)

    assert base.breakdown.monthly_support_average.amount == Decimal("133333")


def test_support_is_capped_at_recurring_cost() -> None:
    payload = _payload()
    payload["supports"] = [
        {
            "name": "과대 합성 지원",
            "monthly_amount": {"amount": "5000000", "currency": "KRW"},
            "start_month": 1,
            "duration_months": 12,
            "status": "confirmed",
            "source_version": "synthetic-v1",
        }
    ]

    result = calculate_costs(CostCalculationInput.model_validate(payload))
    base = _scenario(result, ScenarioKind.BASE)

    assert base.breakdown.contract_total_cost.amount == Decimal("1200000")
    assert "SUPPORT_CAPPED_AT_RECURRING_COST" in base.reason_codes


def test_invalid_own_funds_are_rejected() -> None:
    with pytest.raises(ValidationError, match="cannot exceed deposit"):
        _request(
            {
                "deposit": {"amount": "10000000", "currency": "KRW"},
                "own_funds_for_deposit": {"amount": "11000000", "currency": "KRW"},
            }
        )


def test_invalid_range_is_rejected() -> None:
    with pytest.raises(ValidationError, match="minimum <= base <= maximum"):
        _request(
            {
                "annual_borrowing_rate": {
                    "minimum": "5",
                    "base": "4",
                    "maximum": "3",
                }
            }
        )


def test_cost_api_returns_structured_result() -> None:
    response = TestClient(app).post("/costs/calculate", json=_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "calculated"
    assert len(response.json()["scenarios"]) == 3
    assert response.json()["calculation_version"] == "cost-v1"


def test_cost_api_returns_common_validation_error() -> None:
    payload = _payload()
    payload["monthly_net_income"] = {"amount": "0", "currency": "KRW"}

    response = TestClient(app).post("/costs/calculate", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
