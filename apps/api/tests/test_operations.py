import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from homefit_api.llm import LLMState, LLMStatus
from homefit_api.main import app, get_llm_gateway
from homefit_api.operations import OperationalState, evaluate_readiness, validate_environment
from homefit_api.settings import Settings


class DisabledGateway:
    async def status(self) -> LLMStatus:
        return LLMStatus(
            provider="ollama",
            model="qwen3:4b",
            state=LLMState.DISABLED,
            manual_fallback=True,
            detail="합성 장애: 수동 모드",
        )

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> dict[str, object]:
        del system_prompt, user_prompt, temperature
        raise AssertionError("Readiness must not generate model output")


def test_readiness_keeps_optional_llm_failure_degraded(tmp_path: Path) -> None:
    report = asyncio.run(
        evaluate_readiness(
            Settings(upload_dir=str(tmp_path), llm_enabled=False), DisabledGateway()
        )
    )

    assert report.status is OperationalState.DEGRADED
    checks = {item.code: item for item in report.checks}
    assert checks["storage_writable"].state is OperationalState.READY
    assert checks["local_llm"].required is False
    assert checks["local_llm"].state is OperationalState.DEGRADED


def test_readiness_endpoint_reports_safe_degraded_mode() -> None:
    app.dependency_overrides[get_llm_gateway] = DisabledGateway
    try:
        response = TestClient(app).get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert "database_url" not in response.text
    assert "local_homefit_password" not in response.text


def test_development_environment_is_demo_deployable_with_warnings() -> None:
    report = validate_environment(Settings(llm_enabled=False))

    assert report.deployable is True
    assert report.blockers == []
    assert "persistent_repository" in report.warnings
    assert "manual_release_gates" in report.warnings


def test_insecure_production_environment_is_blocked() -> None:
    report = validate_environment(
        Settings(app_env="production", llm_enabled=False, enable_hsts=False)
    )

    assert report.deployable is False
    assert {"https_hsts", "persistent_repository", "database_secret"}.issubset(
        report.blockers
    )
