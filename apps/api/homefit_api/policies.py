import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from importlib.resources import files
from typing import Annotated, Any

from pydantic import BaseModel, Field, HttpUrl, model_validator

NonNegativeDecimal = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class EligibilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    MISSING_INFORMATION = "MISSING_INFORMATION"
    OFFICIAL_CHECK_NEEDED = "OFFICIAL_CHECK_NEEDED"
    EXPIRED = "EXPIRED"


class ReviewStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"


class ConditionOperator(StrEnum):
    EQUALS = "equals"
    IN = "in"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    GREATER_THAN = "greater_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"


class ConditionFailureStatus(StrEnum):
    INELIGIBLE = "INELIGIBLE"
    OFFICIAL_CHECK_NEEDED = "OFFICIAL_CHECK_NEEDED"


class PolicyCondition(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9_]+$", max_length=80)
    field: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    operator: ConditionOperator
    expected: Decimal | bool | str | list[str]
    failure_status: ConditionFailureStatus = ConditionFailureStatus.INELIGIBLE
    source_section: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=300)


class PolicySource(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    published_on: date | None = None
    checked_on: date
    original_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    locator: str = Field(min_length=1, max_length=300)


class PolicyReview(BaseModel):
    status: ReviewStatus
    author: str = Field(min_length=1, max_length=100)
    reviewer: str | None = Field(default=None, max_length=100)
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_independent_review(self) -> "PolicyReview":
        if self.status is ReviewStatus.APPROVED:
            if not self.reviewer or self.reviewed_at is None:
                raise ValueError("approved rules require reviewer and reviewed_at")
            if self.reviewer.strip().casefold() == self.author.strip().casefold():
                raise ValueError("author and reviewer must be different people")
        return self


class ChecklistItem(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9_]+$", max_length=80)
    action: str = Field(min_length=1, max_length=300)
    source_section: str = Field(min_length=1, max_length=200)


class PolicyBenefit(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9_]+$", max_length=80)
    summary: str = Field(min_length=1, max_length=300)
    maximum_amount: Decimal | None = Field(default=None, ge=0)
    maximum_monthly_amount: Decimal | None = Field(default=None, ge=0)
    duration_months: int | None = Field(default=None, ge=1, le=240)
    amount_note: str = Field(min_length=1, max_length=300)


class PolicyDefinition(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_]+$", max_length=80)
    name: str = Field(min_length=1, max_length=150)
    operator: str = Field(min_length=1, max_length=150)
    document_version: str = Field(min_length=1, max_length=50)
    rule_version: str = Field(min_length=1, max_length=50)
    effective_from: date
    effective_until: date | None = None
    application_from: date | None = None
    application_until: date | None = None
    pass_status: EligibilityStatus
    pass_reason_code: str = Field(pattern=r"^[A-Z0-9_]+$", max_length=80)
    selection_not_guaranteed: bool = True
    regions: list[str] = Field(min_length=1)
    housing_types: list[str] = Field(min_length=1)
    source: PolicySource
    review: PolicyReview
    conditions: list[PolicyCondition] = Field(min_length=1)
    benefits: list[PolicyBenefit] = Field(min_length=1)
    duplicate_benefit_notes: list[str] = Field(min_length=1)
    exceptions: list[str] = Field(default_factory=list)
    official_check_items: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    checklist: list[ChecklistItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates(self) -> "PolicyDefinition":
        if self.effective_until and self.effective_until < self.effective_from:
            raise ValueError("effective_until must not precede effective_from")
        if self.application_from and self.application_until:
            if self.application_until < self.application_from:
                raise ValueError("application_until must not precede application_from")
        return self

    @property
    def is_activatable(self) -> bool:
        return (
            self.review.status is ReviewStatus.APPROVED
            and self.source.original_sha256 is not None
            and self.review.reviewer is not None
            and self.review.reviewed_at is not None
        )


class PolicyCatalog(BaseModel):
    schema_version: str = Field(min_length=1, max_length=30)
    policies: list[PolicyDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_versions(self) -> "PolicyCatalog":
        keys = [(policy.code, policy.document_version, policy.rule_version) for policy in self.policies]
        if len(keys) != len(set(keys)):
            raise ValueError("policy code and versions must be unique")
        return self


class EligibilityInput(BaseModel):
    as_of_date: date
    age_years: int | None = Field(default=None, ge=0, le=120)
    region: str | None = Field(default=None, max_length=50)
    household_size: int | None = Field(default=None, ge=1, le=20)
    annual_household_gross_income: NonNegativeDecimal | None = None
    median_income_ratio_percent: NonNegativeDecimal | None = None
    net_assets: NonNegativeDecimal | None = None
    is_homeless: bool | None = None
    is_household_head: bool | None = None
    is_single_household_head: bool | None = None
    deposit: NonNegativeDecimal | None = None
    monthly_rent: NonNegativeDecimal | None = None
    area_sqm: NonNegativeDecimal | None = None
    lease_deposit_paid_ratio_percent: NonNegativeDecimal | None = None
    received_same_seoul_support_before: bool | None = None
    receiving_other_monthly_rent_support: bool | None = None
    has_conflicting_fund_loan: bool | None = None
    applicant_type: str | None = Field(default=None, max_length=50)

    def evaluation_context(self) -> dict[str, object | None]:
        context = self.model_dump()
        deposit = self.deposit
        rent = self.monthly_rent
        context["seoul_adjusted_monthly_rent"] = (
            None if deposit is None or rent is None else rent + deposit * Decimal("0.045") / 12
        )
        return context


class ConditionResult(BaseModel):
    code: str
    field: str
    passed: bool | None
    actual: Decimal | bool | str | None
    expected: Decimal | bool | str | list[str]
    source_section: str
    reason_code: str


class PolicyReference(BaseModel):
    title: str
    url: HttpUrl
    checked_on: date
    original_sha256: str | None
    locator: str


class EligibilityResult(BaseModel):
    policy_code: str
    policy_name: str
    status: EligibilityStatus
    reason_codes: list[str]
    missing_fields: list[str]
    checks: list[ConditionResult]
    document_version: str
    rule_version: str
    source: PolicyReference
    evaluated_as_of: date
    input_sha256: str
    selection_not_guaranteed: bool
    official_check_items: list[str]


class PolicySummary(BaseModel):
    code: str
    name: str
    operator: str
    document_version: str
    rule_version: str
    review_status: ReviewStatus
    activatable: bool
    source: PolicyReference


class PolicyNotFoundError(LookupError):
    pass


def _input_sha256(payload: EligibilityInput) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("condition value is not numeric") from error


def _condition_matches(actual: object, condition: PolicyCondition) -> bool:
    expected = condition.expected
    if condition.operator is ConditionOperator.EQUALS:
        return actual == expected
    if condition.operator is ConditionOperator.IN:
        return isinstance(expected, list) and str(actual) in expected
    if isinstance(expected, (bool, list)):
        raise ValueError(f"{condition.code} requires a numeric expected value")
    actual_number = _as_decimal(actual)
    expected_number = _as_decimal(expected)
    if condition.operator is ConditionOperator.GREATER_THAN_OR_EQUAL:
        return actual_number >= expected_number
    if condition.operator is ConditionOperator.GREATER_THAN:
        return actual_number > expected_number
    return actual_number <= expected_number


def _reference(policy: PolicyDefinition) -> PolicyReference:
    return PolicyReference(
        title=policy.source.title,
        url=policy.source.url,
        checked_on=policy.source.checked_on,
        original_sha256=policy.source.original_sha256,
        locator=policy.source.locator,
    )


def _eligibility_result(
    policy: PolicyDefinition,
    payload: EligibilityInput,
    *,
    status: EligibilityStatus,
    reason_codes: list[str],
    missing_fields: list[str],
    checks: list[ConditionResult],
) -> EligibilityResult:
    return EligibilityResult(
        policy_code=policy.code,
        policy_name=policy.name,
        status=status,
        reason_codes=reason_codes,
        missing_fields=missing_fields,
        checks=checks,
        document_version=policy.document_version,
        rule_version=policy.rule_version,
        source=_reference(policy),
        evaluated_as_of=payload.as_of_date,
        input_sha256=_input_sha256(payload),
        selection_not_guaranteed=policy.selection_not_guaranteed,
        official_check_items=policy.official_check_items,
    )


def evaluate_policy(policy: PolicyDefinition, payload: EligibilityInput) -> EligibilityResult:
    if not policy.is_activatable:
        return _eligibility_result(
            policy,
            payload,
            status=EligibilityStatus.OFFICIAL_CHECK_NEEDED,
            reason_codes=["POLICY_REVIEW_PENDING"],
            missing_fields=[],
            checks=[],
        )
    if policy.effective_until and payload.as_of_date > policy.effective_until:
        return _eligibility_result(
            policy,
            payload,
            status=EligibilityStatus.EXPIRED,
            reason_codes=["POLICY_VERSION_EXPIRED"],
            missing_fields=[],
            checks=[],
        )
    if payload.as_of_date < policy.effective_from:
        return _eligibility_result(
            policy,
            payload,
            status=EligibilityStatus.OFFICIAL_CHECK_NEEDED,
            reason_codes=["POLICY_NOT_YET_EFFECTIVE"],
            missing_fields=[],
            checks=[],
        )
    if policy.application_until and payload.as_of_date > policy.application_until:
        return _eligibility_result(
            policy,
            payload,
            status=EligibilityStatus.EXPIRED,
            reason_codes=["APPLICATION_PERIOD_ENDED"],
            missing_fields=[],
            checks=[],
        )
    if policy.application_from and payload.as_of_date < policy.application_from:
        return _eligibility_result(
            policy,
            payload,
            status=EligibilityStatus.OFFICIAL_CHECK_NEEDED,
            reason_codes=["APPLICATION_NOT_OPEN"],
            missing_fields=[],
            checks=[],
        )

    context = payload.evaluation_context()
    checks: list[ConditionResult] = []
    missing_fields: list[str] = []
    failure_statuses: list[ConditionFailureStatus] = []
    for condition in policy.conditions:
        actual = context.get(condition.field)
        if actual is None:
            missing_fields.append(condition.field)
            checks.append(
                ConditionResult(
                    code=condition.code,
                    field=condition.field,
                    passed=None,
                    actual=None,
                    expected=condition.expected,
                    source_section=condition.source_section,
                    reason_code=f"{condition.code}_MISSING",
                )
            )
            continue
        passed = _condition_matches(actual, condition)
        checks.append(
            ConditionResult(
                code=condition.code,
                field=condition.field,
                passed=passed,
                actual=actual if isinstance(actual, (Decimal, bool, str)) else str(actual),
                expected=condition.expected,
                source_section=condition.source_section,
                reason_code=f"{condition.code}_{'PASSED' if passed else 'FAILED'}",
            )
        )
        if not passed:
            failure_statuses.append(condition.failure_status)

    if missing_fields:
        status = EligibilityStatus.MISSING_INFORMATION
        reasons = ["REQUIRED_INPUT_MISSING"]
    elif ConditionFailureStatus.INELIGIBLE in failure_statuses:
        status = EligibilityStatus.INELIGIBLE
        reasons = [check.reason_code for check in checks if check.passed is False]
    elif failure_statuses:
        status = EligibilityStatus.OFFICIAL_CHECK_NEEDED
        reasons = [check.reason_code for check in checks if check.passed is False]
    else:
        status = policy.pass_status
        reasons = [policy.pass_reason_code]

    return _eligibility_result(
        policy,
        payload,
        status=status,
        reason_codes=reasons,
        missing_fields=sorted(set(missing_fields)),
        checks=checks,
    )


class PolicyRegistry:
    def __init__(self, catalog: PolicyCatalog) -> None:
        self.catalog = catalog
        self._by_code = {policy.code: policy for policy in catalog.policies}

    @classmethod
    def load_default(cls) -> "PolicyRegistry":
        resource = files("homefit_api.policy_data").joinpath("policy_catalog.json")
        catalog = PolicyCatalog.model_validate_json(resource.read_text(encoding="utf-8"))
        return cls(catalog)

    def list_policies(self) -> list[PolicySummary]:
        return [
            PolicySummary(
                code=policy.code,
                name=policy.name,
                operator=policy.operator,
                document_version=policy.document_version,
                rule_version=policy.rule_version,
                review_status=policy.review.status,
                activatable=policy.is_activatable,
                source=_reference(policy),
            )
            for policy in self.catalog.policies
        ]

    def get(self, code: str) -> PolicyDefinition:
        try:
            return self._by_code[code]
        except KeyError as error:
            raise PolicyNotFoundError(code) from error


def policy_json_schema() -> dict[str, Any]:
    return PolicyCatalog.model_json_schema()


def changed_policy_codes(previous: PolicyCatalog, current: PolicyCatalog) -> list[str]:
    """Return policy codes whose source or rule version changed between catalogs."""

    old_versions = {
        policy.code: (policy.document_version, policy.rule_version) for policy in previous.policies
    }
    new_versions = {
        policy.code: (policy.document_version, policy.rule_version) for policy in current.policies
    }
    return sorted(
        code
        for code in old_versions.keys() | new_versions.keys()
        if old_versions.get(code) != new_versions.get(code)
    )
