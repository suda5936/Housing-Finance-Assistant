import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from homefit_api.cost_api import router as cost_router
from homefit_api.data_api import repository
from homefit_api.data_api import router as data_router
from homefit_api.document_api import router as document_router
from homefit_api.errors import register_error_handlers
from homefit_api.llm import LanguageModelGateway, LLMStatus, OllamaGateway
from homefit_api.observability import RequestSafetyMiddleware
from homefit_api.observability import router as system_router
from homefit_api.operations import OperationalState, ReadinessReport, evaluate_readiness
from homefit_api.orchestration_api import router as orchestration_router
from homefit_api.policy_api import router as policy_router
from homefit_api.privacy import configure_privacy_logging
from homefit_api.rag_api import router as rag_router
from homefit_api.ranking_api import router as ranking_router
from homefit_api.settings import get_settings

settings = get_settings()
configure_privacy_logging()
logger = logging.getLogger("homefit_api.retention")


async def _retention_loop() -> None:
    while True:
        await asyncio.sleep(settings.retention_purge_interval_seconds)
        purged = repository.purge_expired()
        if purged:
            logger.info({"event": "retention_purge", "sessions_deleted": purged})


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    retention_task = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        retention_task.cancel()
        with suppress(asyncio.CancelledError):
            await retention_task

app = FastAPI(
    title="집결정 AI API",
    summary="청년 주거 의사결정을 위한 검증 가능한 API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    RequestSafetyMiddleware,
    max_request_bytes=settings.max_request_bytes,
    use_hsts=settings.enable_hsts,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=[
        "Content-Type",
        "Idempotency-Key",
        "X-Request-ID",
        "X-Session-Token",
    ],
    expose_headers=["X-Request-ID"],
)
app.include_router(data_router)
app.include_router(document_router)
app.include_router(cost_router)
app.include_router(policy_router)
app.include_router(ranking_router)
app.include_router(rag_router)
app.include_router(orchestration_router)
app.include_router(system_router)
register_error_handlers(app)


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


@app.get("/ready", response_model=ReadinessReport, tags=["system"])
async def readiness(
    response: Response,
    gateway: Annotated[LanguageModelGateway, Depends(get_llm_gateway)],
) -> ReadinessReport:
    """Report required components and safe degraded-mode availability."""

    report = await evaluate_readiness(settings, gateway)
    if report.status is OperationalState.BLOCKED:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report


@app.get("/system/llm", response_model=LLMStatus, tags=["system"])
async def llm_status(
    gateway: Annotated[LanguageModelGateway, Depends(get_llm_gateway)],
) -> LLMStatus:
    """Report local model readiness without failing the manual workflow."""

    return await gateway.status()
