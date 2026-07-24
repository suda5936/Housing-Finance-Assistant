import hashlib
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from homefit_api.data import Currency, Money

Percentage = Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)]
WON = Decimal("1")
PERCENT = Decimal("0.01")
ONE_HUNDRED = Decimal("100")
MONTHS_PER_YEAR = Decimal("12")
CALCULATION_VERSION = "cost-v1"


class CalculationStatus(StrEnum):
    CALCULATED = "calculated"
    MISSING_INFORMATION = "missing_information"


class ScenarioKind(StrEnum):
    OPTIMISTIC = "optimistic"
    BASE = "base"
    CONSERVATIVE = "conservative"


class SupportStatus(StrEnum):
    CONFIRMED = "confirmed"
    EXPECTED = "expected"


class CommuteSource(StrEnum):
    MANUAL = "manual"
    MAP = "map"


class MoneyRange(BaseModel):
    minimum: Money
    base: Money
    maximum: Money

    @model_validator(mode="after")
    def validate_range(self) -> "MoneyRange":
        currencies = {self.minimum.currency, self.base.currency, self.maximum.currency}
        if currencies != {Currency.KRW}:
            raise ValueError("Only KRW is supported")
        if not self.minimum.amount <= self.base.amount <= self.maximum.amount:
            raise ValueError("Money range must satisfy minimum <= base <= maximum")
        return self


class RateRange(BaseModel):
    minimum: Percentage
    base: Percentage
    maximum: Percentage

    @model_validator(mode="after")
    def validate_range(self) -> "RateRange":
        if not self.minimum <= self.base <= self.maximum:
            raise ValueError("Rate range must satisfy minimum <= base <= maximum")
        return self


class CommuteInput(BaseModel):
    source: CommuteSource = CommuteSource.MANUAL
    transport_mode: str = Field(min_length=1, max_length=30)
    reference_at: datetime
    commute_minutes_one_way: int = Field(ge=0, le=600)
    monthly_cost: Money | None = None
    round_trip_cost: Money | None = None
    workdays_per_month: int | None = Field(default=None, ge=0, le=31)

    @model_validator(mode="after")
    def validate_cost_input(self) -> "CommuteInput":
        has_monthly = self.monthly_cost is not None
        has_round_trip = self.round_trip_cost is not None
        if has_monthly == has_round_trip:
            raise ValueError("Provide either monthly_cost or round_trip_cost")
        if has_round_trip and self.workdays_per_month is None:
            raise ValueError("workdays_per_month is required with round_trip_cost")
        return self


class SupportItem(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    monthly_amount: Money
    start_month: int = Field(default=1, ge=1, le=120)
    duration_months: int = Field(ge=1, le=120)
    status: SupportStatus
    source_version: str = Field(min_length=1, max_length=100)


class CostCalculationInput(BaseModel):
    candidate_label: str = Field(min_length=1, max_length=50)
    candidate_district: str = Field(min_length=1, max_length=50)
    monthly_net_income: Money
    deposit: Money
    own_funds_for_deposit: Money
    monthly_rent: Money
    monthly_maintenance: MoneyRange | None
    annual_borrowing_rate: RateRange
    annual_own_funds_opportunity_rate: RateRange = RateRange(
        minimum=Decimal("0"),
        base=Decimal("0"),
        maximum=Decimal("0"),
    )
    contract_months: int = Field(ge=1, le=120)
    initial_costs: Money
    commute: CommuteInput | None
    monthly_living_cost: Money = Money(amount=Decimal("0"))
    supports: list[SupportItem] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_consistency(self) -> "CostCalculationInput":
        currencies = {
            self.monthly_net_income.currency,
            self.deposit.currency,
            self.own_funds_for_deposit.currency,
            self.monthly_rent.currency,
            self.initial_costs.currency,
            self.monthly_living_cost.currency,
        }
        if currencies != {Currency.KRW}:
            raise ValueError("Only KRW is supported")
        if self.monthly_net_income.amount <= 0:
            raise ValueError("monthly_net_income must be greater than zero")
        if self.own_funds_for_deposit.amount > self.deposit.amount:
            raise ValueError("own_funds_for_deposit cannot exceed deposit")
        return self


class CostBreakdown(BaseModel):
    monthly_rent: Money
    monthly_maintenance: Money
    monthly_borrowing_cost: Money
    monthly_opportunity_cost: Money
    monthly_commute_cost: Money
    monthly_living_cost: Money
    monthly_initial_cost_amortization: Money
    monthly_support_average: Money
    monthly_effective_cost: Money
    contract_total_cost: Money
    housing_cost_burden_percent: Decimal


class CalculationAssumptions(BaseModel):
    scenario: ScenarioKind
    candidate_district: str
    contract_months: int
    borrowed_deposit: Money
    refundable_deposit_excluded: bool
    annual_borrowing_rate_percent: Decimal
    annual_opportunity_rate_percent: Decimal
    maintenance_amount: Money
    commute_source: CommuteSource
    commute_transport_mode: str
    commute_reference_at: datetime
    commute_minutes_one_way: int
    workdays_per_month: int | None
    expected_support_included: bool


class CostScenarioResult(BaseModel):
    scenario: ScenarioKind
    breakdown: CostBreakdown
    assumptions: CalculationAssumptions
    reason_codes: list[str]


class CostCalculationResponse(BaseModel):
    status: CalculationStatus
    calculation_version: str
    input_sha256: str
    missing_fields: list[str]
    scenarios: list[CostScenarioResult]


def _money(amount: Decimal) -> Money:
    return Money(amount=amount.quantize(WON, rounding=ROUND_HALF_UP), currency=Currency.KRW)


def _percentage(value: Decimal) -> Decimal:
    return value.quantize(PERCENT, rounding=ROUND_HALF_UP)


def _input_sha256(payload: CostCalculationInput) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _select_range_value(
    scenario: ScenarioKind,
    *,
    minimum: Decimal,
    base: Decimal,
    maximum: Decimal,
) -> Decimal:
    if scenario is ScenarioKind.OPTIMISTIC:
        return minimum
    if scenario is ScenarioKind.CONSERVATIVE:
        return maximum
    return base


def _monthly_commute_cost(commute: CommuteInput) -> Decimal:
    if commute.monthly_cost is not None:
        return commute.monthly_cost.amount
    if commute.round_trip_cost is None or commute.workdays_per_month is None:
        raise ValueError("Incomplete commute cost input")
    return commute.round_trip_cost.amount * commute.workdays_per_month


def _support_total(
    supports: list[SupportItem],
    *,
    contract_months: int,
    include_expected: bool,
) -> Decimal:
    total = Decimal("0")
    for support in supports:
        if support.status is SupportStatus.EXPECTED and not include_expected:
            continue
        available_months = max(0, contract_months - support.start_month + 1)
        applied_months = min(available_months, support.duration_months)
        total += support.monthly_amount.amount * applied_months
    return total


def _calculate_scenario(
    payload: CostCalculationInput,
    scenario: ScenarioKind,
) -> CostScenarioResult:
    if payload.monthly_maintenance is None or payload.commute is None:
        raise ValueError("Required calculation fields are missing")

    maintenance = _select_range_value(
        scenario,
        minimum=payload.monthly_maintenance.minimum.amount,
        base=payload.monthly_maintenance.base.amount,
        maximum=payload.monthly_maintenance.maximum.amount,
    )
    borrowing_rate = _select_range_value(
        scenario,
        minimum=payload.annual_borrowing_rate.minimum,
        base=payload.annual_borrowing_rate.base,
        maximum=payload.annual_borrowing_rate.maximum,
    )
    opportunity_rate = _select_range_value(
        scenario,
        minimum=payload.annual_own_funds_opportunity_rate.minimum,
        base=payload.annual_own_funds_opportunity_rate.base,
        maximum=payload.annual_own_funds_opportunity_rate.maximum,
    )
    borrowed_deposit = payload.deposit.amount - payload.own_funds_for_deposit.amount
    monthly_borrowing_cost = (
        borrowed_deposit * borrowing_rate / ONE_HUNDRED / MONTHS_PER_YEAR
    )
    monthly_opportunity_cost = (
        payload.own_funds_for_deposit.amount
        * opportunity_rate
        / ONE_HUNDRED
        / MONTHS_PER_YEAR
    )
    monthly_commute = _monthly_commute_cost(payload.commute)
    recurring_before_support = (
        payload.monthly_rent.amount
        + maintenance
        + monthly_borrowing_cost
        + monthly_opportunity_cost
        + monthly_commute
        + payload.monthly_living_cost.amount
    )
    recurring_contract_total = recurring_before_support * payload.contract_months
    include_expected_support = scenario is ScenarioKind.OPTIMISTIC
    requested_support_total = _support_total(
        payload.supports,
        contract_months=payload.contract_months,
        include_expected=include_expected_support,
    )
    applied_support_total = min(requested_support_total, recurring_contract_total)
    contract_total = (
        recurring_contract_total + payload.initial_costs.amount - applied_support_total
    )
    monthly_effective = contract_total / payload.contract_months
    monthly_initial = payload.initial_costs.amount / payload.contract_months
    monthly_support = applied_support_total / payload.contract_months
    burden_percent = monthly_effective / payload.monthly_net_income.amount * ONE_HUNDRED

    reason_codes = ["REFUNDABLE_DEPOSIT_EXCLUDED"]
    if payload.monthly_maintenance.minimum.amount != payload.monthly_maintenance.maximum.amount:
        reason_codes.append("MAINTENANCE_RANGE_SCENARIO_APPLIED")
    if opportunity_rate > 0:
        reason_codes.append("OWN_FUNDS_OPPORTUNITY_COST_INCLUDED")
    if payload.commute.source is CommuteSource.MANUAL:
        reason_codes.append("MANUAL_COMMUTE_INPUT_USED")
    if any(support.status is SupportStatus.EXPECTED for support in payload.supports):
        reason_codes.append(
            "EXPECTED_SUPPORT_INCLUDED"
            if include_expected_support
            else "EXPECTED_SUPPORT_EXCLUDED"
        )
    if applied_support_total < requested_support_total:
        reason_codes.append("SUPPORT_CAPPED_AT_RECURRING_COST")

    return CostScenarioResult(
        scenario=scenario,
        breakdown=CostBreakdown(
            monthly_rent=_money(payload.monthly_rent.amount),
            monthly_maintenance=_money(maintenance),
            monthly_borrowing_cost=_money(monthly_borrowing_cost),
            monthly_opportunity_cost=_money(monthly_opportunity_cost),
            monthly_commute_cost=_money(monthly_commute),
            monthly_living_cost=_money(payload.monthly_living_cost.amount),
            monthly_initial_cost_amortization=_money(monthly_initial),
            monthly_support_average=_money(monthly_support),
            monthly_effective_cost=_money(monthly_effective),
            contract_total_cost=_money(contract_total),
            housing_cost_burden_percent=_percentage(burden_percent),
        ),
        assumptions=CalculationAssumptions(
            scenario=scenario,
            candidate_district=payload.candidate_district,
            contract_months=payload.contract_months,
            borrowed_deposit=_money(borrowed_deposit),
            refundable_deposit_excluded=True,
            annual_borrowing_rate_percent=borrowing_rate,
            annual_opportunity_rate_percent=opportunity_rate,
            maintenance_amount=_money(maintenance),
            commute_source=payload.commute.source,
            commute_transport_mode=payload.commute.transport_mode,
            commute_reference_at=payload.commute.reference_at,
            commute_minutes_one_way=payload.commute.commute_minutes_one_way,
            workdays_per_month=payload.commute.workdays_per_month,
            expected_support_included=include_expected_support,
        ),
        reason_codes=reason_codes,
    )


def calculate_costs(payload: CostCalculationInput) -> CostCalculationResponse:
    missing_fields: list[str] = []
    if payload.monthly_maintenance is None:
        missing_fields.append("monthly_maintenance")
    if payload.commute is None:
        missing_fields.append("commute")
    if missing_fields:
        return CostCalculationResponse(
            status=CalculationStatus.MISSING_INFORMATION,
            calculation_version=CALCULATION_VERSION,
            input_sha256=_input_sha256(payload),
            missing_fields=missing_fields,
            scenarios=[],
        )

    scenarios = [
        _calculate_scenario(payload, scenario)
        for scenario in (
            ScenarioKind.OPTIMISTIC,
            ScenarioKind.BASE,
            ScenarioKind.CONSERVATIVE,
        )
    ]
    return CostCalculationResponse(
        status=CalculationStatus.CALCULATED,
        calculation_version=CALCULATION_VERSION,
        input_sha256=_input_sha256(payload),
        missing_fields=[],
        scenarios=scenarios,
    )
