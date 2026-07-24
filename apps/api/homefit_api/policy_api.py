from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from homefit_api.policies import (
    EligibilityInput,
    EligibilityResult,
    PolicyNotFoundError,
    PolicyRegistry,
    PolicySummary,
    evaluate_policy,
    policy_json_schema,
)

router = APIRouter(prefix="/policies", tags=["policies"])
_registry = PolicyRegistry.load_default()


def get_policy_registry() -> PolicyRegistry:
    return _registry


@router.get("", response_model=list[PolicySummary])
def list_policies(
    registry: Annotated[PolicyRegistry, Depends(get_policy_registry)],
) -> list[PolicySummary]:
    """List versioned policies and whether human review permits activation."""

    return registry.list_policies()


@router.get("/schema", response_model=dict[str, Any])
def get_policy_schema() -> dict[str, Any]:
    """Expose the JSON Schema used to validate policy catalogs."""

    return policy_json_schema()


@router.post("/{policy_code}/eligibility", response_model=EligibilityResult)
def check_eligibility(
    policy_code: str,
    payload: EligibilityInput,
    registry: Annotated[PolicyRegistry, Depends(get_policy_registry)],
) -> EligibilityResult:
    """Evaluate pre-eligibility deterministically without calling an LLM."""

    try:
        policy = registry.get(policy_code)
    except PolicyNotFoundError as error:
        raise HTTPException(status_code=404, detail="등록되지 않은 정책입니다.") from error
    return evaluate_policy(policy, payload)
