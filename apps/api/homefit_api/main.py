from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from homefit_api.llm import LanguageModelGateway, LLMStatus, OllamaGateway
from homefit_api.settings import get_settings

settings = get_settings()

app = FastAPI(
    title="집결정 AI API",
    summary="청년 주거 의사결정을 위한 검증 가능한 API",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Idempotency-Key"],
)


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """Return service health without exposing sensitive configuration."""

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env,
    )


def get_llm_gateway() -> LanguageModelGateway:
    return OllamaGateway(settings)


@app.get("/system/llm", response_model=LLMStatus, tags=["system"])
async def llm_status(
    gateway: Annotated[LanguageModelGateway, Depends(get_llm_gateway)],
) -> LLMStatus:
    """Report local model readiness without failing the manual workflow."""

    return await gateway.status()
