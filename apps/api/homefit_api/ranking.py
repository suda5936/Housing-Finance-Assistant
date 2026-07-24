import hashlib
import json
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from homefit_api.costs import ScenarioKind
from homefit_api.policies import EligibilityStatus

Score = Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)]
Weight = Annotated[Decimal, Field(ge=0, le=60, max_digits=7, decimal_places=4)]
NonNegative = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
RANKING_VERSION = "ranking-v1"
SCORE_UNIT = Decimal("0.01")
WEIGHT_UNIT = Decimal("0.0001")
TIE_TOLERANCE = Decimal("0.01")


class Criterion(StrEnum):
    MONTHLY_EFFECTIVE_COST = "monthly_effective_cost"
    COMMUTE_MINUTES = "commute_minutes"
    DEPOSIT = "deposit"
    AREA_SQM = "area_sqm"
    RISK_SCORE = "risk_score"
    INFRASTRUCTURE_SCORE = "infrastructure_score"


class Direction(StrEnum):
    LOWER_IS_BETTER = "lower_is_better"
    HIGHER_IS_BETTER = "higher_is_better"


class InfrastructureSource(StrEnum):
    USER = "user"
    OPEN_DATA = "open_data"
    NOT_EVALUATED = "not_evaluated"


class RankingStatus(StrEnum):
    RANKED = "ranked"
    PARTIAL = "partial"
    NOT_COMPARABLE = "not_comparable"


class CandidateDisposition(StrEnum):
    RANKED = "ranked"
    HARD_CONSTRAINT_FAILED = "hard_constraint_failed"
    NOT_COMPARABLE = "not_comparable"


class RankingWeights(BaseModel):
    monthly_effective_cost: Weight = Decimal("40")
    commute_minutes: Weight = Decimal("25")
    deposit: Weight = Decimal("15")
    area_sqm: Weight = Decimal("10")
    risk_score: Weight = Decimal("10")
    infrastructure_score: Weight = Decimal("0")

    @model_validator(mode="after")
    def validate_total(self) -> "RankingWeights":
        if sum(self.as_dict().values(), Decimal("0")) != Decimal("100"):
            raise ValueError("weights must sum to exactly 100")
        return self

    def as_dict(self) -> dict[Criterion, Decimal]:
        return {criterion: getattr(self, criterion.value) for criterion in Criterion}


class InfrastructureEvidence(BaseModel):
    source: InfrastructureSource
    methodology: str = Field(min_length=1, max_length=300)
    reference_at: datetime
    source_reference: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def reject_unevaluated_evidence(self) -> "InfrastructureEvidence":
        if self.source is InfrastructureSource.NOT_EVALUATED:
            raise ValueError("not_evaluated cannot be used as evidence")
        return self


class RankingCandidateInput(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    district: str = Field(min_length=1, max_length=50)
    monthly_effective_cost: NonNegative | None = None
    cost_scenario: ScenarioKind = ScenarioKind.BASE
    cost_calculation_version: str | None = Field(default=None, max_length=50)
    cost_input_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    commute_minutes: NonNegative | None = None
    commute_reference_at: datetime | None = None
    deposit: NonNegative | None = None
    area_sqm: NonNegative | None = None
    risk_score: Score | None = None
    risk_basis: str | None = Field(default=None, max_length=300)
    infrastructure_score: Score | None = None
    infrastructure_evidence: InfrastructureEvidence | None = None
    policy_statuses: list[EligibilityStatus] = Field(default_factory=list)
    policy_versions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_provenance(self) -> "RankingCandidateInput":
        if self.monthly_effective_cost is not None:
            if not self.cost_calculation_version or not self.cost_input_sha256:
                raise ValueError("calculated cost requires calculation version and input hash")
        if self.commute_minutes is not None and self.commute_reference_at is None:
            raise ValueError("commute_reference_at is required with commute_minutes")
        if self.risk_score is not None and not self.risk_basis:
            raise ValueError("risk_basis is required with risk_score")
        if (self.infrastructure_score is None) != (self.infrastructure_evidence is None):
            raise ValueError("infrastructure score and evidence must be provided together")
        return self


class HardConstraints(BaseModel):
    max_monthly_effective_cost: NonNegative | None = None
    max_commute_minutes: NonNegative | None = None
    max_deposit: NonNegative | None = None
    min_area_sqm: NonNegative | None = None
    required_districts: list[str] = Field(default_factory=list)


class RankingRequest(BaseModel):
    candidates: list[RankingCandidateInput] = Field(min_length=2, max_length=10)
    weights: RankingWeights = Field(default_factory=RankingWeights)
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    sensitivity_delta_points: Decimal = Field(default=Decimal("10"), ge=0, le=30)

    @model_validator(mode="after")
    def validate_candidate_ids(self) -> "RankingRequest":
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate_id values must be unique")
        scenarios = {candidate.cost_scenario for candidate in self.candidates}
        if len(scenarios) != 1:
            raise ValueError("all candidates must use the same cost scenario")
        return self


class CriterionContribution(BaseModel):
    criterion: Criterion
    direction: Direction
    raw_value: Decimal
    normalized_score: Decimal
    weight_percent: Decimal
    contribution: Decimal


class CandidateProvenance(BaseModel):
    cost_scenario: ScenarioKind
    cost_calculation_version: str | None
    cost_input_sha256: str | None
    commute_reference_at: datetime | None
    risk_basis: str | None
    infrastructure_evidence: InfrastructureEvidence | None
    policy_statuses: list[EligibilityStatus]
    policy_versions: list[str]


class CandidateRankingResult(BaseModel):
    candidate_id: str
    label: str
    disposition: CandidateDisposition
    rank: int | None
    total_score: Decimal | None
    contributions: list[CriterionContribution]
    hard_constraint_failures: list[str]
    missing_fields: list[str]
    dominated_by: list[str]
    reason_codes: list[str]
    provenance: CandidateProvenance


class Tradeoff(BaseModel):
    candidate_id: str
    compared_with_id: str
    advantages: list[Criterion]
    disadvantages: list[Criterion]


class SensitivityScenario(BaseModel):
    changed_criterion: Criterion
    delta_points: Decimal
    weights: RankingWeights
    winner_ids: list[str]
    ranks: dict[str, int]


class CandidateSensitivity(BaseModel):
    candidate_id: str
    best_rank: int
    worst_rank: int
    top_rank_stable: bool


class SensitivityAnalysis(BaseModel):
    delta_points: Decimal
    scenarios: list[SensitivityScenario]
    candidates: list[CandidateSensitivity]
    winner_changes: bool


class RankingResponse(BaseModel):
    status: RankingStatus
    ranking_version: str
    input_sha256: str
    weights: RankingWeights
    results: list[CandidateRankingResult]
    tradeoffs: list[Tradeoff]
    sensitivity: SensitivityAnalysis | None
    warnings: list[str]


DIRECTIONS = {
    Criterion.MONTHLY_EFFECTIVE_COST: Direction.LOWER_IS_BETTER,
    Criterion.COMMUTE_MINUTES: Direction.LOWER_IS_BETTER,
    Criterion.DEPOSIT: Direction.LOWER_IS_BETTER,
    Criterion.AREA_SQM: Direction.HIGHER_IS_BETTER,
    Criterion.RISK_SCORE: Direction.LOWER_IS_BETTER,
    Criterion.INFRASTRUCTURE_SCORE: Direction.HIGHER_IS_BETTER,
}


def _input_sha256(payload: RankingRequest) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _value(candidate: RankingCandidateInput, criterion: Criterion) -> Decimal | None:
    value = getattr(candidate, criterion.value)
    return value if isinstance(value, Decimal) else None


def _hard_constraint_failures(
    candidate: RankingCandidateInput, constraints: HardConstraints
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    missing: list[str] = []
    checks = (
        ("monthly_effective_cost", candidate.monthly_effective_cost, constraints.max_monthly_effective_cost, "max"),
        ("commute_minutes", candidate.commute_minutes, constraints.max_commute_minutes, "max"),
        ("deposit", candidate.deposit, constraints.max_deposit, "max"),
        ("area_sqm", candidate.area_sqm, constraints.min_area_sqm, "min"),
    )
    for field, actual, limit, kind in checks:
        if limit is None:
            continue
        if actual is None:
            missing.append(field)
        elif (kind == "max" and actual > limit) or (kind == "min" and actual < limit):
            failures.append(f"HARD_{field.upper()}_{kind.upper()}_FAILED")
    if constraints.required_districts and candidate.district not in constraints.required_districts:
        failures.append("HARD_REQUIRED_DISTRICT_FAILED")
    return failures, missing


def _required_missing(
    candidate: RankingCandidateInput, weights: dict[Criterion, Decimal]
) -> list[str]:
    return [
        criterion.value
        for criterion, weight in weights.items()
        if weight > 0 and _value(candidate, criterion) is None
    ]


def _normalize(
    candidates: list[RankingCandidateInput], weights: dict[Criterion, Decimal]
) -> dict[str, dict[Criterion, Decimal]]:
    normalized: dict[str, dict[Criterion, Decimal]] = {
        candidate.candidate_id: {} for candidate in candidates
    }
    for criterion in weights:
        values = [_value(candidate, criterion) for candidate in candidates]
        if any(value is None for value in values):
            continue
        numeric = [value for value in values if value is not None]
        minimum = min(numeric)
        maximum = max(numeric)
        for candidate in candidates:
            value = _value(candidate, criterion)
            if value is None:
                continue
            if maximum == minimum:
                score = Decimal("50")
            elif DIRECTIONS[criterion] is Direction.LOWER_IS_BETTER:
                score = (maximum - value) / (maximum - minimum) * 100
            else:
                score = (value - minimum) / (maximum - minimum) * 100
            normalized[candidate.candidate_id][criterion] = score
    return normalized


def _scores(
    candidate_ids: list[str],
    normalized: dict[str, dict[Criterion, Decimal]],
    weights: dict[Criterion, Decimal],
) -> dict[str, Decimal]:
    return {
        candidate_id: sum(
            (
                normalized[candidate_id][criterion] * weight / 100
                for criterion, weight in weights.items()
                if weight > 0
            ),
            Decimal("0"),
        )
        for candidate_id in candidate_ids
    }


def _ranks(scores: dict[str, Decimal]) -> dict[str, int]:
    ordered = sorted(scores, key=lambda item: (-scores[item], item))
    ranks: dict[str, int] = {}
    current_rank = 0
    rank_anchor_score: Decimal | None = None
    for index, candidate_id in enumerate(ordered, start=1):
        score = scores[candidate_id]
        if rank_anchor_score is None or abs(rank_anchor_score - score) > TIE_TOLERANCE:
            current_rank = index
            rank_anchor_score = score
        ranks[candidate_id] = current_rank
    return ranks


def _dominates(
    first: RankingCandidateInput,
    second: RankingCandidateInput,
    criteria: list[Criterion],
) -> bool:
    at_least_as_good = True
    strictly_better = False
    for criterion in criteria:
        first_value = _value(first, criterion)
        second_value = _value(second, criterion)
        if first_value is None or second_value is None:
            return False
        if DIRECTIONS[criterion] is Direction.LOWER_IS_BETTER:
            at_least_as_good &= first_value <= second_value
            strictly_better |= first_value < second_value
        else:
            at_least_as_good &= first_value >= second_value
            strictly_better |= first_value > second_value
    return at_least_as_good and strictly_better


def _adjust_weights(
    base: RankingWeights, criterion: Criterion, delta: Decimal
) -> RankingWeights | None:
    values = base.as_dict()
    old_target = values[criterion]
    supported_others = {
        item for item in Criterion if item is not criterion and values[item] > 0
    }
    new_target = min(Decimal("60"), max(Decimal("0"), old_target + delta))
    if new_target == old_target:
        return None
    other_total = Decimal("100") - old_target
    new_other_total = Decimal("100") - new_target
    for item in Criterion:
        if item is criterion:
            values[item] = new_target
        else:
            values[item] = values[item] / other_total * new_other_total
    while True:
        over_limit = [
            item for item in Criterion if item is not criterion and values[item] > 60
        ]
        if not over_limit:
            break
        excess = sum((values[item] - Decimal("60") for item in over_limit), Decimal("0"))
        for item in over_limit:
            values[item] = Decimal("60")
        receivers = [item for item in supported_others if values[item] < 60]
        if not receivers:
            return None
        while excess > 0 and receivers:
            share = excess / len(receivers)
            distributed = Decimal("0")
            next_receivers = []
            for item in receivers:
                addition = min(Decimal("60") - values[item], share)
                values[item] += addition
                distributed += addition
                if values[item] < 60:
                    next_receivers.append(item)
            if distributed == 0:
                return None
            excess -= distributed
            receivers = next_receivers
    rounded = {item: value.quantize(WEIGHT_UNIT) for item, value in values.items()}
    residual = Decimal("100") - sum(rounded.values(), Decimal("0"))
    receiver = next(
        item
        for item in supported_others
        if (rounded[item] < 60 if residual > 0 else rounded[item] > 0)
    )
    rounded[receiver] += residual
    return RankingWeights(**{item.value: value for item, value in rounded.items()})


def _sensitivity(
    request: RankingRequest,
    candidates: list[RankingCandidateInput],
    normalized: dict[str, dict[Criterion, Decimal]],
    base_ranks: dict[str, int],
) -> SensitivityAnalysis:
    ids = [candidate.candidate_id for candidate in candidates]
    scenarios: list[SensitivityScenario] = []
    rank_samples = {candidate_id: [base_ranks[candidate_id]] for candidate_id in ids}
    base_winners = {candidate_id for candidate_id, rank in base_ranks.items() if rank == 1}
    winner_changes = False
    for criterion in Criterion:
        if any(criterion not in normalized[candidate_id] for candidate_id in ids):
            continue
        for signed_delta in (-request.sensitivity_delta_points, request.sensitivity_delta_points):
            adjusted = _adjust_weights(request.weights, criterion, signed_delta)
            if adjusted is None:
                continue
            scenario_scores = _scores(ids, normalized, adjusted.as_dict())
            scenario_ranks = _ranks(scenario_scores)
            winners = sorted(candidate_id for candidate_id, rank in scenario_ranks.items() if rank == 1)
            winner_changes |= set(winners) != base_winners
            for candidate_id, rank in scenario_ranks.items():
                rank_samples[candidate_id].append(rank)
            scenarios.append(
                SensitivityScenario(
                    changed_criterion=criterion,
                    delta_points=signed_delta,
                    weights=adjusted,
                    winner_ids=winners,
                    ranks=scenario_ranks,
                )
            )
    return SensitivityAnalysis(
        delta_points=request.sensitivity_delta_points,
        scenarios=scenarios,
        candidates=[
            CandidateSensitivity(
                candidate_id=candidate_id,
                best_rank=min(rank_samples[candidate_id]),
                worst_rank=max(rank_samples[candidate_id]),
                top_rank_stable=all(rank == 1 for rank in rank_samples[candidate_id]),
            )
            for candidate_id in ids
        ],
        winner_changes=winner_changes,
    )


def _time_warnings(candidates: list[RankingCandidateInput]) -> list[str]:
    warnings: list[str] = []
    commute_times = [
        candidate.commute_reference_at
        for candidate in candidates
        if candidate.commute_reference_at is not None
    ]
    if commute_times and max(commute_times) - min(commute_times) > timedelta(days=30):
        warnings.append("COMMUTE_REFERENCE_TIME_MISMATCH_OVER_30_DAYS")
    infrastructure_times = [
        candidate.infrastructure_evidence.reference_at
        for candidate in candidates
        if candidate.infrastructure_evidence is not None
    ]
    if infrastructure_times and max(infrastructure_times) - min(infrastructure_times) > timedelta(days=30):
        warnings.append("INFRASTRUCTURE_REFERENCE_TIME_MISMATCH_OVER_30_DAYS")
    calculation_versions = {
        candidate.cost_calculation_version
        for candidate in candidates
        if candidate.cost_calculation_version is not None
    }
    if len(calculation_versions) > 1:
        warnings.append("MIXED_COST_CALCULATION_VERSIONS")
    return warnings


def rank_candidates(request: RankingRequest) -> RankingResponse:
    weights = request.weights.as_dict()
    pending: list[CandidateRankingResult] = []
    comparable: list[RankingCandidateInput] = []
    for candidate in request.candidates:
        failures, constraint_missing = _hard_constraint_failures(
            candidate, request.hard_constraints
        )
        scoring_missing = _required_missing(candidate, weights)
        missing = sorted(set(constraint_missing + scoring_missing))
        if failures:
            disposition = CandidateDisposition.HARD_CONSTRAINT_FAILED
            reasons = ["HARD_CONSTRAINT_FAILED", *failures]
        elif missing:
            disposition = CandidateDisposition.NOT_COMPARABLE
            reasons = ["REQUIRED_CRITERIA_MISSING"]
        else:
            disposition = CandidateDisposition.RANKED
            reasons = []
            comparable.append(candidate)
        pending.append(
            CandidateRankingResult(
                candidate_id=candidate.candidate_id,
                label=candidate.label,
                disposition=disposition,
                rank=None,
                total_score=None,
                contributions=[],
                hard_constraint_failures=failures,
                missing_fields=missing,
                dominated_by=[],
                reason_codes=reasons,
                provenance=CandidateProvenance(
                    cost_scenario=candidate.cost_scenario,
                    cost_calculation_version=candidate.cost_calculation_version,
                    cost_input_sha256=candidate.cost_input_sha256,
                    commute_reference_at=candidate.commute_reference_at,
                    risk_basis=candidate.risk_basis,
                    infrastructure_evidence=candidate.infrastructure_evidence,
                    policy_statuses=candidate.policy_statuses,
                    policy_versions=candidate.policy_versions,
                ),
            )
        )

    warnings = _time_warnings(comparable)
    if not comparable:
        return RankingResponse(
            status=RankingStatus.NOT_COMPARABLE,
            ranking_version=RANKING_VERSION,
            input_sha256=_input_sha256(request),
            weights=request.weights,
            results=pending,
            tradeoffs=[],
            sensitivity=None,
            warnings=warnings,
        )

    normalized = _normalize(comparable, weights)
    ids = [candidate.candidate_id for candidate in comparable]
    scores = _scores(ids, normalized, weights)
    ranks = _ranks(scores)
    criteria = [criterion for criterion, weight in weights.items() if weight > 0]
    by_id = {candidate.candidate_id: candidate for candidate in comparable}
    result_by_id = {result.candidate_id: result for result in pending}
    for candidate in comparable:
        result = result_by_id[candidate.candidate_id]
        result.rank = ranks[candidate.candidate_id]
        result.contributions = [
            CriterionContribution(
                criterion=criterion,
                direction=DIRECTIONS[criterion],
                raw_value=_value(candidate, criterion) or Decimal("0"),
                normalized_score=normalized[candidate.candidate_id][criterion].quantize(
                    SCORE_UNIT, rounding=ROUND_HALF_UP
                ),
                weight_percent=weight,
                contribution=(
                    normalized[candidate.candidate_id][criterion] * weight / 100
                ).quantize(SCORE_UNIT, rounding=ROUND_HALF_UP),
            )
            for criterion, weight in weights.items()
            if weight > 0
        ]
        result.total_score = sum(
            (part.contribution for part in result.contributions), Decimal("0")
        ).quantize(SCORE_UNIT, rounding=ROUND_HALF_UP)
        result.dominated_by = sorted(
            other.candidate_id
            for other in comparable
            if other.candidate_id != candidate.candidate_id
            and _dominates(other, candidate, criteria)
        )
        if result.dominated_by:
            result.reason_codes.append("PARETO_DOMINATED")
        if len(comparable) == 1:
            result.reason_codes.append("ONLY_FEASIBLE_CANDIDATE")

    winner_ids = sorted(candidate_id for candidate_id, rank in ranks.items() if rank == 1)
    primary_winner = winner_ids[0]
    tradeoffs: list[Tradeoff] = []
    for candidate_id in ids:
        if candidate_id == primary_winner:
            continue
        advantages: list[Criterion] = []
        disadvantages: list[Criterion] = []
        for criterion in criteria:
            current = _value(by_id[candidate_id], criterion)
            winner = _value(by_id[primary_winner], criterion)
            if current is None or winner is None or current == winner:
                continue
            current_better = (
                current < winner
                if DIRECTIONS[criterion] is Direction.LOWER_IS_BETTER
                else current > winner
            )
            (advantages if current_better else disadvantages).append(criterion)
        tradeoffs.append(
            Tradeoff(
                candidate_id=candidate_id,
                compared_with_id=primary_winner,
                advantages=advantages,
                disadvantages=disadvantages,
            )
        )

    sensitivity = _sensitivity(request, comparable, normalized, ranks)
    status = RankingStatus.RANKED if len(comparable) == len(request.candidates) else RankingStatus.PARTIAL
    if len(comparable) == 1:
        status = RankingStatus.PARTIAL
    return RankingResponse(
        status=status,
        ranking_version=RANKING_VERSION,
        input_sha256=_input_sha256(request),
        weights=request.weights,
        results=sorted(
            pending,
            key=lambda result: (
                result.rank is None,
                result.rank if result.rank is not None else 999,
                result.candidate_id,
            ),
        ),
        tradeoffs=tradeoffs,
        sensitivity=sensitivity,
        warnings=warnings,
    )
