from copy import deepcopy
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from homefit_api.main import app
from homefit_api.policies import (
    EligibilityInput,
    EligibilityStatus,
    PolicyCatalog,
    PolicyDefinition,
    PolicyRegistry,
    changed_policy_codes,
    evaluate_policy,
)


def _approved(policy: PolicyDefinition) -> PolicyDefinition:
    data = policy.model_dump(mode="json")
    data["source"]["original_sha256"] = "a" * 64
    data["review"] = {
        "status": "approved",
        "author": "rule-author",
        "reviewer": "independent-reviewer",
        "reviewed_at": datetime(2026, 7, 24, tzinfo=UTC).isoformat(),
    }
    return PolicyDefinition.model_validate(data)


def _seoul_payload(**overrides: object) -> EligibilityInput:
    values: dict[str, object] = {
        "as_of_date": date(2026, 5, 10),
        "age_years": 25,
        "region": "서울특별시",
        "median_income_ratio_percent": "100",
        "is_homeless": True,
        "deposit": "10000000",
        "monthly_rent": "500000",
        "received_same_seoul_support_before": False,
        "receiving_other_monthly_rent_support": False,
    }
    values.update(overrides)
    return EligibilityInput.model_validate(values)


def _loan_payload(**overrides: object) -> EligibilityInput:
    values: dict[str, object] = {
        "as_of_date": date(2026, 7, 24),
        "age_years": 25,
        "annual_household_gross_income": "40000000",
        "net_assets": "200000000",
        "is_homeless": True,
        "is_single_household_head": True,
        "area_sqm": "40",
        "lease_deposit_paid_ratio_percent": "5",
        "has_conflicting_fund_loan": False,
    }
    values.update(overrides)
    return EligibilityInput.model_validate(values)


def test_default_catalog_contains_three_review_gated_policies() -> None:
    registry = PolicyRegistry.load_default()
    summaries = registry.list_policies()

    assert len(summaries) == 3
    assert all(not item.activatable for item in summaries)
    assert all(item.source.original_sha256 is None for item in summaries)
    assert all(policy.regions for policy in registry.catalog.policies)
    assert all(policy.housing_types for policy in registry.catalog.policies)
    assert all(policy.benefits for policy in registry.catalog.policies)
    assert all(policy.duplicate_benefit_notes for policy in registry.catalog.policies)


def test_unreviewed_policy_never_exposes_eligibility_result() -> None:
    policy = PolicyRegistry.load_default().get("seoul_youth_monthly_rent_2026")

    result = evaluate_policy(policy, _seoul_payload())

    assert result.status is EligibilityStatus.OFFICIAL_CHECK_NEEDED
    assert result.reason_codes == ["POLICY_REVIEW_PENDING"]
    assert result.checks == []


def test_approved_seoul_policy_returns_basic_eligibility_not_selection_guarantee() -> None:
    policy = _approved(PolicyRegistry.load_default().get("seoul_youth_monthly_rent_2026"))

    result = evaluate_policy(policy, _seoul_payload())

    assert result.status is EligibilityStatus.ELIGIBLE
    assert result.selection_not_guaranteed is True
    assert result.reason_codes == ["BASIC_REQUIREMENTS_POSSIBLY_MET"]
    assert all(check.passed is True for check in result.checks)


def test_missing_information_is_not_treated_as_ineligible() -> None:
    policy = _approved(PolicyRegistry.load_default().get("seoul_youth_monthly_rent_2026"))

    result = evaluate_policy(policy, _seoul_payload(deposit=None))

    assert result.status is EligibilityStatus.MISSING_INFORMATION
    assert "deposit" in result.missing_fields
    assert "seoul_adjusted_monthly_rent" in result.missing_fields


@pytest.mark.parametrize(
    ("overrides", "failed_code"),
    [
        ({"age_years": 40}, "SEOUL_AGE_MAX_FAILED"),
        ({"median_income_ratio_percent": "48"}, "SEOUL_INCOME_MIN_FAILED"),
        ({"deposit": "80000001"}, "SEOUL_DEPOSIT_MAX_FAILED"),
        ({"region": "경기도"}, "SEOUL_REGION_FAILED"),
    ],
)
def test_seoul_policy_boundary_failures(
    overrides: dict[str, object], failed_code: str
) -> None:
    policy = _approved(PolicyRegistry.load_default().get("seoul_youth_monthly_rent_2026"))

    result = evaluate_policy(policy, _seoul_payload(**overrides))

    assert result.status is EligibilityStatus.INELIGIBLE
    assert failed_code in result.reason_codes


def test_seoul_upper_income_boundary_and_rent_conversion_pass() -> None:
    policy = _approved(PolicyRegistry.load_default().get("seoul_youth_monthly_rent_2026"))

    result = evaluate_policy(
        policy,
        _seoul_payload(
            median_income_ratio_percent="150",
            deposit="20000000",
            monthly_rent="800000",
        ),
    )

    assert result.status is EligibilityStatus.ELIGIBLE


def test_application_period_expiry_precedes_condition_evaluation() -> None:
    policy = _approved(PolicyRegistry.load_default().get("seoul_youth_monthly_rent_2026"))

    result = evaluate_policy(policy, _seoul_payload(as_of_date=date(2026, 7, 24)))

    assert result.status is EligibilityStatus.EXPIRED
    assert result.reason_codes == ["APPLICATION_PERIOD_ENDED"]
    assert result.checks == []


def test_loan_basic_requirements_still_require_official_bank_review() -> None:
    policy = _approved(PolicyRegistry.load_default().get("youth_deposit_monthly_loan"))

    result = evaluate_policy(policy, _loan_payload())

    assert result.status is EligibilityStatus.OFFICIAL_CHECK_NEEDED
    assert result.reason_codes == ["BASIC_REQUIREMENTS_MET_BANK_REVIEW_REQUIRED"]
    assert "자산심사" in result.official_check_items


def test_same_input_is_reproducible_and_carries_source_versions() -> None:
    policy = _approved(PolicyRegistry.load_default().get("youth_deposit_monthly_loan"))

    first = evaluate_policy(policy, _loan_payload())
    second = evaluate_policy(policy, _loan_payload())

    assert first == second
    assert first.input_sha256 == second.input_sha256
    assert first.document_version == policy.document_version
    assert first.rule_version == policy.rule_version
    assert first.source.original_sha256 == "a" * 64


def test_approval_rejects_self_review() -> None:
    policy = PolicyRegistry.load_default().get("youth_deposit_monthly_loan")
    data = policy.model_dump(mode="json")
    data["review"] = {
        "status": "approved",
        "author": "same-person",
        "reviewer": "same-person",
        "reviewed_at": datetime(2026, 7, 24, tzinfo=UTC).isoformat(),
    }

    with pytest.raises(ValidationError):
        PolicyDefinition.model_validate(data)


def test_catalog_change_identifies_affected_policy() -> None:
    previous = PolicyRegistry.load_default().catalog
    current_data = deepcopy(previous.model_dump(mode="json"))
    current_data["policies"][1]["rule_version"] = "youth-deposit-loan-draft-v2"
    current = PolicyCatalog.model_validate(current_data)

    assert changed_policy_codes(previous, current) == ["youth_deposit_monthly_loan"]


def test_policy_api_lists_schema_evaluates_gate_and_returns_404() -> None:
    client = TestClient(app)

    listed = client.get("/policies")
    schema = client.get("/policies/schema")
    evaluated = client.post(
        "/policies/seoul_youth_monthly_rent_2026/eligibility",
        json=_seoul_payload().model_dump(mode="json"),
    )
    missing = client.post(
        "/policies/not_registered/eligibility",
        json=_seoul_payload().model_dump(mode="json"),
    )

    assert listed.status_code == 200
    assert len(listed.json()) == 3
    assert schema.status_code == 200
    assert schema.json()["title"] == "PolicyCatalog"
    assert evaluated.status_code == 200
    assert evaluated.json()["status"] == "OFFICIAL_CHECK_NEEDED"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"
