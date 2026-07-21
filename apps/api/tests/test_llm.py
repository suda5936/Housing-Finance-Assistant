import asyncio

import httpx
from fastapi.testclient import TestClient

from homefit_api.llm import LLMState, LLMStatus, OllamaGateway
from homefit_api.main import app, get_llm_gateway
from homefit_api.settings import Settings


class ReadyGateway:
    async def status(self) -> LLMStatus:
        return LLMStatus(
            provider="ollama",
            model="qwen3:4b",
            state=LLMState.READY,
            manual_fallback=False,
            detail="로컬 LLM을 사용할 수 있습니다.",
        )

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> dict[str, object]:
        return {"ok": True}


def test_llm_status_endpoint_uses_gateway_boundary() -> None:
    app.dependency_overrides[get_llm_gateway] = ReadyGateway
    try:
        response = TestClient(app).get("/system/llm")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "provider": "ollama",
        "model": "qwen3:4b",
        "state": "ready",
        "manual_fallback": False,
        "detail": "로컬 LLM을 사용할 수 있습니다.",
    }


def test_ollama_gateway_reports_missing_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "another-model:latest"}]})

    gateway = OllamaGateway(Settings(), transport=httpx.MockTransport(handler))
    status = asyncio.run(gateway.status())

    assert status.state is LLMState.MODEL_MISSING
    assert status.manual_fallback is True


def test_ollama_gateway_reports_ready_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})

    gateway = OllamaGateway(Settings(), transport=httpx.MockTransport(handler))
    status = asyncio.run(gateway.status())

    assert status.state is LLMState.READY
    assert status.manual_fallback is False


def test_ollama_gateway_reports_disabled_model() -> None:
    gateway = OllamaGateway(Settings(llm_enabled=False))
    status = asyncio.run(gateway.status())

    assert status.state is LLMState.DISABLED
    assert status.manual_fallback is True


def test_ollama_gateway_reports_unavailable_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    gateway = OllamaGateway(Settings(), transport=httpx.MockTransport(handler))
    status = asyncio.run(gateway.status())

    assert status.state is LLMState.UNAVAILABLE
    assert status.manual_fallback is True


def test_ollama_gateway_generates_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        request_body = request.read().decode()
        assert '"model":"qwen3:4b"' in request_body
        assert '"format":"json"' in request_body
        return httpx.Response(200, json={"message": {"content": '{"status":"ready"}'}})

    gateway = OllamaGateway(Settings(), transport=httpx.MockTransport(handler))
    result = asyncio.run(
        gateway.generate_json(
            system_prompt="Return JSON.",
            user_prompt="Report status.",
        )
    )

    assert result == {"status": "ready"}
