import json
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.resources import files
from typing import Any

from pydantic import BaseModel, Field, model_validator

from homefit_api.costs import CostCalculationInput, ScenarioKind, calculate_costs
from homefit_api.documents import contains_prompt_injection
from homefit_api.policies import (
    EligibilityInput,
    PolicyDefinition,
    PolicyRegistry,
    evaluate_policy,
)
from homefit_api.rag import (
    PolicyEvidenceRegistry,
    RetrievalEvaluationCase,
    evaluate_retrieval,
)
from homefit_api.ranking import RankingRequest, rank_candidates

EVALUATION_VERSION = "release-evaluator-v1"
SYNTHETIC_SOURCE_HASH = "a" * 64
RANKING_SOURCE_HASH = "b" * 64


class CostCase(BaseModel):
    id: str
    input: dict[str, Any]
    expected: dict[str, str]


class PolicyCase(BaseModel):
    id: str
    approved_fixture: bool
    expected_status: str
    input: dict[str, Any]


class RankingCandidateCase(BaseModel):
    id: str
    cost: str
    commute: str
    deposit: str
    area: str
    risk: str


class RankingCase(BaseModel):
    id: str
    candidates: list[RankingCandidateCase] = Field(min_length=2)
    expected_order: list[str]
    expected_scores: list[str]
    expected_ranks: list[int] | None = None


class DocumentSafetyCase(BaseModel):
    id: str
    text: str
    expected_injection: bool


class ManualGate(BaseModel):
    id: str
    description: str
    completed: bool
    evidence: str | None = None

    @model_validator(mode="after")
    def require_evidence_when_completed(self) -> "ManualGate":
        if self.completed and not self.evidence:
            raise ValueError("A completed manual gate requires an evidence reference")
        return self


class ReleaseDataset(BaseModel):
    dataset_version: str
    evaluated_as_of: date
    cost_cases: list[CostCase]
    policy_cases: list[PolicyCase]
    ranking_cases: list[RankingCase]
    retrieval_cases: list[RetrievalEvaluationCase]
    document_safety_cases: list[DocumentSafetyCase]
    manual_gates: list[ManualGate]


class EvaluationCheck(BaseModel):
    id: str
    area: str
    passed: bool
    blocking: bool = True
    expected: str
    observed: str


class AreaMetric(BaseModel):
    area: str
    passed: int = Field(ge=0)
    total: int = Field(ge=0)
    accuracy: Decimal = Field(ge=0, le=1)


class ReleaseEvaluationReport(BaseModel):
    evaluator_version: str
    dataset_version: str
    evaluated_as_of: date
    checks: list[EvaluationCheck]
    metrics: list[AreaMetric]
    automated_blockers: list[str]
    pending_manual_gates: list[ManualGate]
    automated_gate_passed: bool
    release_ready: bool


def load_release_dataset() -> ReleaseDataset:
    resource = files("homefit_api.evaluation_data").joinpath("release_cases.json")
    return ReleaseDataset.model_validate_json(resource.read_text(encoding="utf-8"))


def _check(
    *,
    case_id: str,
    area: str,
    expected: object,
    observed: object,
) -> EvaluationCheck:
    return EvaluationCheck(
        id=case_id,
        area=area,
        passed=expected == observed,
        expected=str(expected),
        observed=str(observed),
    )


def _approved_synthetic_policy(policy: PolicyDefinition) -> PolicyDefinition:
    data = policy.model_dump(mode="json")
    data["source"]["original_sha256"] = SYNTHETIC_SOURCE_HASH
    data["review"] = {
        "status": "approved",
        "author": "synthetic-evaluation-author",
        "reviewer": "synthetic-independent-reviewer",
        "reviewed_at": datetime(2026, 7, 25, tzinfo=UTC).isoformat(),
    }
    return PolicyDefinition.model_validate(data)


def _cost_checks(dataset: ReleaseDataset) -> list[EvaluationCheck]:
    checks: list[EvaluationCheck] = []
    for case in dataset.cost_cases:
        result = calculate_costs(CostCalculationInput.model_validate(case.input))
        checks.append(
            _check(
                case_id=f"{case.id}:status",
                area="cost",
                expected=case.expected["status"],
                observed=result.status.value,
            )
        )
        base = next(item for item in result.scenarios if item.scenario is ScenarioKind.BASE)
        observed = {
            "monthly_effective_cost": str(base.breakdown.monthly_effective_cost.amount),
            "contract_total_cost": str(base.breakdown.contract_total_cost.amount),
            "housing_cost_burden_percent": str(
                base.breakdown.housing_cost_burden_percent
            ),
        }
        for field, expected in case.expected.items():
            if field == "status":
                continue
            checks.append(
                _check(
                    case_id=f"{case.id}:{field}",
                    area="cost",
                    expected=expected,
                    observed=observed[field],
                )
            )
    return checks


def _policy_checks(dataset: ReleaseDataset) -> list[EvaluationCheck]:
    registry = PolicyRegistry.load_default()
    source_policy = registry.get("seoul_youth_monthly_rent_2026")
    checks = []
    for case in dataset.policy_cases:
        policy = (
            _approved_synthetic_policy(source_policy)
            if case.approved_fixture
            else source_policy
        )
        result = evaluate_policy(policy, EligibilityInput.model_validate(case.input))
        checks.append(
            _check(
                case_id=case.id,
                area="policy",
                expected=case.expected_status,
                observed=result.status.value,
            )
        )
    return checks


def _ranking_payload(case: RankingCase) -> dict[str, object]:
    candidates = [
        {
            "candidate_id": item.id,
            "label": f"합성 후보 {item.id}",
            "district": "서울 마포구",
            "monthly_effective_cost": item.cost,
            "cost_scenario": "base",
            "cost_calculation_version": "cost-v1",
            "cost_input_sha256": RANKING_SOURCE_HASH,
            "commute_minutes": item.commute,
            "commute_reference_at": "2026-07-24T00:00:00Z",
            "deposit": item.deposit,
            "area_sqm": item.area,
            "risk_score": item.risk,
            "risk_basis": "합성 위험신호 평가",
            "policy_statuses": ["OFFICIAL_CHECK_NEEDED"],
            "policy_versions": ["policy-draft-v1"],
        }
        for item in case.candidates
    ]
    return {"candidates": candidates}


def _ranking_checks(dataset: ReleaseDataset) -> list[EvaluationCheck]:
    checks = []
    for case in dataset.ranking_cases:
        result = rank_candidates(RankingRequest.model_validate(_ranking_payload(case)))
        observed_order = [item.candidate_id for item in result.results]
        observed_scores = [str(item.total_score) for item in result.results]
        checks.extend(
            [
                _check(
                    case_id=f"{case.id}:order",
                    area="ranking",
                    expected=case.expected_order,
                    observed=observed_order,
                ),
                _check(
                    case_id=f"{case.id}:scores",
                    area="ranking",
                    expected=case.expected_scores,
                    observed=observed_scores,
                ),
            ]
        )
        if case.expected_ranks is not None:
            checks.append(
                _check(
                    case_id=f"{case.id}:ranks",
                    area="ranking",
                    expected=case.expected_ranks,
                    observed=[item.rank for item in result.results],
                )
            )
    return checks


def _retrieval_checks(dataset: ReleaseDataset) -> list[EvaluationCheck]:
    result = evaluate_retrieval(
        PolicyEvidenceRegistry.load_default(), dataset.retrieval_cases
    )
    return [
        _check(
            case_id="retrieval:hit-rate-at-k",
            area="retrieval",
            expected=Decimal("1"),
            observed=result.hit_rate_at_k,
        ),
        _check(
            case_id="retrieval:citation-alignment",
            area="retrieval",
            expected=Decimal("1"),
            observed=result.citation_alignment,
        ),
    ]


def _document_checks(dataset: ReleaseDataset) -> list[EvaluationCheck]:
    return [
        _check(
            case_id=case.id,
            area="document_safety",
            expected=case.expected_injection,
            observed=contains_prompt_injection(case.text),
        )
        for case in dataset.document_safety_cases
    ]


def _metrics(checks: list[EvaluationCheck]) -> list[AreaMetric]:
    areas = sorted({check.area for check in checks})
    metrics = []
    for area in areas:
        area_checks = [check for check in checks if check.area == area]
        passed = sum(check.passed for check in area_checks)
        metrics.append(
            AreaMetric(
                area=area,
                passed=passed,
                total=len(area_checks),
                accuracy=Decimal(passed) / Decimal(len(area_checks)),
            )
        )
    return metrics


def evaluate_release(dataset: ReleaseDataset | None = None) -> ReleaseEvaluationReport:
    selected = dataset or load_release_dataset()
    checks = [
        *_cost_checks(selected),
        *_policy_checks(selected),
        *_ranking_checks(selected),
        *_retrieval_checks(selected),
        *_document_checks(selected),
    ]
    blockers = [check.id for check in checks if check.blocking and not check.passed]
    manual = [gate for gate in selected.manual_gates if not gate.completed]
    return ReleaseEvaluationReport(
        evaluator_version=EVALUATION_VERSION,
        dataset_version=selected.dataset_version,
        evaluated_as_of=selected.evaluated_as_of,
        checks=checks,
        metrics=_metrics(checks),
        automated_blockers=blockers,
        pending_manual_gates=manual,
        automated_gate_passed=not blockers,
        release_ready=not blockers and not manual,
    )


def main() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    report = evaluate_release()
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    if report.automated_blockers:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
