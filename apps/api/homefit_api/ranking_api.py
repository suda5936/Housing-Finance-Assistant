from fastapi import APIRouter

from homefit_api.ranking import RankingRequest, RankingResponse, rank_candidates

router = APIRouter(prefix="/rankings", tags=["rankings"])


@router.post("/compare", response_model=RankingResponse)
def compare_candidates(payload: RankingRequest) -> RankingResponse:
    """Rank housing candidates deterministically without using an LLM."""

    return rank_candidates(payload)
