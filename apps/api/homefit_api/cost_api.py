from fastapi import APIRouter

from homefit_api.costs import CostCalculationInput, CostCalculationResponse, calculate_costs

router = APIRouter(prefix="/costs", tags=["costs"])


@router.post("/calculate", response_model=CostCalculationResponse)
def calculate_cost(payload: CostCalculationInput) -> CostCalculationResponse:
    """Calculate deterministic housing-cost scenarios without using an LLM."""

    return calculate_costs(payload)

