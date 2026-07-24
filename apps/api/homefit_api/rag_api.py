from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from homefit_api.policies import EligibilityInput, PolicyNotFoundError, PolicyRegistry
from homefit_api.policy_api import get_policy_registry
from homefit_api.rag import (
    GroundedEligibilityResult,
    PolicyEvidenceRegistry,
    SearchRequest,
    SearchResponse,
    ground_eligibility,
)

router = APIRouter(tags=["policy-evidence"])
_evidence_registry = PolicyEvidenceRegistry.load_default()


def get_evidence_registry() -> PolicyEvidenceRegistry:
    return _evidence_registry


@router.post("/policy-evidence/search", response_model=SearchResponse)
def search_policy_evidence(
    payload: SearchRequest,
    evidence: Annotated[PolicyEvidenceRegistry, Depends(get_evidence_registry)],
) -> SearchResponse:
    """Search allow-listed official policy snapshots without invoking an LLM."""

    return evidence.search(payload)


@router.post(
    "/policies/{policy_code}/eligibility-with-evidence",
    response_model=GroundedEligibilityResult,
)
def check_eligibility_with_evidence(
    policy_code: str,
    payload: EligibilityInput,
    policies: Annotated[PolicyRegistry, Depends(get_policy_registry)],
    evidence: Annotated[PolicyEvidenceRegistry, Depends(get_evidence_registry)],
) -> GroundedEligibilityResult:
    """Attach exact official-source passages to every evaluated policy condition."""

    try:
        policy = policies.get(policy_code)
    except PolicyNotFoundError as error:
        raise HTTPException(status_code=404, detail="등록되지 않은 정책입니다.") from error
    return ground_eligibility(policy, payload, evidence)
