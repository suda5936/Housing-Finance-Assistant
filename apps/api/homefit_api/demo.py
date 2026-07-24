import json
import sys
from datetime import datetime
from decimal import Decimal
from importlib.resources import files

from pydantic import BaseModel, Field

from homefit_api.costs import CostCalculationInput, ScenarioKind, calculate_costs
from homefit_api.documents import TextBlock, contains_prompt_injection, extract_structured_fields
from homefit_api.ranking import RankingRequest, RankingWeights, rank_candidates


class DemoProfile(BaseModel):
    monthly_income: str
    own_funds: str
    annual_rate: str
    initial_costs: str


class DemoCandidate(BaseModel):
    id: str
    label: str
    district: str
    deposit: str
    rent: str
    maintenance: str
    area: str
    months: int = Field(ge=1, le=120)
    commute_minutes: int = Field(ge=0)
    commute_cost: str
    risk: str
    risk_basis: str


class DemoScenario(BaseModel):
    id: str
    title: str
    profile: DemoProfile
    weights: RankingWeights
    candidates: list[DemoCandidate] = Field(min_length=2, max_length=3)
    document_text: str
    expected_winner: str


class DemoDataset(BaseModel):
    dataset_version: str
    reference_at: datetime
    scenarios: list[DemoScenario] = Field(min_length=3)


class DemoCandidateResult(BaseModel):
    id: str
    monthly_effective_cost: Decimal
    contract_total_cost: Decimal


class DemoResult(BaseModel):
    id: str
    title: str
    winner: str
    expected_winner: str
    reproducible: bool
    candidates: list[DemoCandidateResult]
    extracted_fields: list[str]
    document_safe: bool
    external_services_required: bool = False


class DemoReport(BaseModel):
    dataset_version: str
    scenarios: list[DemoResult]
    all_reproducible: bool


def load_demo_dataset() -> DemoDataset:
    resource = files("homefit_api.demo_data").joinpath("scenarios.json")
    return DemoDataset.model_validate_json(resource.read_text(encoding="utf-8"))


def _money(amount: str) -> dict[str, str]:
    return {"amount": amount, "currency": "KRW"}


def _cost_input(
    scenario: DemoScenario, candidate: DemoCandidate, reference_at: datetime
) -> CostCalculationInput:
    own_funds = min(Decimal(scenario.profile.own_funds), Decimal(candidate.deposit))
    rate = scenario.profile.annual_rate
    maintenance = _money(candidate.maintenance)
    return CostCalculationInput.model_validate(
        {
            "candidate_label": candidate.label,
            "candidate_district": candidate.district,
            "monthly_net_income": _money(scenario.profile.monthly_income),
            "deposit": _money(candidate.deposit),
            "own_funds_for_deposit": _money(str(own_funds)),
            "monthly_rent": _money(candidate.rent),
            "monthly_maintenance": {
                "minimum": maintenance,
                "base": maintenance,
                "maximum": maintenance,
            },
            "annual_borrowing_rate": {
                "minimum": rate,
                "base": rate,
                "maximum": rate,
            },
            "contract_months": candidate.months,
            "initial_costs": _money(scenario.profile.initial_costs),
            "commute": {
                "source": "manual",
                "transport_mode": "public_transit",
                "reference_at": reference_at,
                "commute_minutes_one_way": candidate.commute_minutes,
                "monthly_cost": _money(candidate.commute_cost),
            },
        }
    )


def evaluate_demo_scenario(
    scenario: DemoScenario, reference_at: datetime
) -> DemoResult:
    cost_results = {
        candidate.id: calculate_costs(_cost_input(scenario, candidate, reference_at))
        for candidate in scenario.candidates
    }
    ranking_candidates = []
    candidate_results = []
    for candidate in scenario.candidates:
        cost = cost_results[candidate.id]
        base = next(item for item in cost.scenarios if item.scenario is ScenarioKind.BASE)
        candidate_results.append(
            DemoCandidateResult(
                id=candidate.id,
                monthly_effective_cost=base.breakdown.monthly_effective_cost.amount,
                contract_total_cost=base.breakdown.contract_total_cost.amount,
            )
        )
        ranking_candidates.append(
            {
                "candidate_id": candidate.id,
                "label": candidate.label,
                "district": candidate.district,
                "monthly_effective_cost": base.breakdown.monthly_effective_cost.amount,
                "cost_scenario": "base",
                "cost_calculation_version": cost.calculation_version,
                "cost_input_sha256": cost.input_sha256,
                "commute_minutes": candidate.commute_minutes,
                "commute_reference_at": reference_at,
                "deposit": candidate.deposit,
                "area_sqm": candidate.area,
                "risk_score": candidate.risk,
                "risk_basis": candidate.risk_basis,
                "policy_statuses": ["OFFICIAL_CHECK_NEEDED"],
                "policy_versions": ["seoul-rent-2026-draft-v1"],
            }
        )
    ranking = rank_candidates(
        RankingRequest.model_validate(
            {"candidates": ranking_candidates, "weights": scenario.weights}
        )
    )
    winner = next(item.candidate_id for item in ranking.results if item.rank == 1)
    block = TextBlock(
        id=f"{scenario.id}-document",
        page=1,
        text=scenario.document_text,
        confidence=Decimal("0.99"),
    )
    fields, contradictions = extract_structured_fields(scenario.document_text, [block])
    safe = not contains_prompt_injection(scenario.document_text) and not contradictions
    return DemoResult(
        id=scenario.id,
        title=scenario.title,
        winner=winner,
        expected_winner=scenario.expected_winner,
        reproducible=winner == scenario.expected_winner,
        candidates=candidate_results,
        extracted_fields=sorted(field.name.value for field in fields),
        document_safe=safe,
    )


def evaluate_demo_dataset(dataset: DemoDataset | None = None) -> DemoReport:
    selected = dataset or load_demo_dataset()
    scenarios = [
        evaluate_demo_scenario(scenario, selected.reference_at)
        for scenario in selected.scenarios
    ]
    return DemoReport(
        dataset_version=selected.dataset_version,
        scenarios=scenarios,
        all_reproducible=all(
            item.reproducible and item.document_safe for item in scenarios
        ),
    )


def main() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    report = evaluate_demo_dataset()
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    if not report.all_reproducible:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
