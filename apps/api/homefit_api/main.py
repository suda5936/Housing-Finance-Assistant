from fastapi import FastAPI
from pydantic import BaseModel

from homefit_api.settings import get_settings

settings = get_settings()

app = FastAPI(
    title="집결정 AI API",
    summary="청년 주거 의사결정을 위한 검증 가능한 API",
    version="0.1.0",
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
