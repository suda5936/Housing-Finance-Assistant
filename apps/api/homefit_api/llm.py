import json
from enum import StrEnum
from typing import Protocol

import httpx
from pydantic import BaseModel

from homefit_api.settings import Settings


class LLMState(StrEnum):
    READY = "ready"
    DISABLED = "disabled"
    MODEL_MISSING = "model_missing"
    UNAVAILABLE = "unavailable"


class LLMStatus(BaseModel):
    provider: str
    model: str
    state: LLMState
    manual_fallback: bool
    detail: str


class LanguageModelGateway(Protocol):
    async def status(self) -> LLMStatus: ...

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> dict[str, object]: ...


class OllamaGateway:
    """Small Ollama adapter; business rules must stay outside this class."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._settings.ollama_base_url,
            timeout=self._settings.llm_timeout_seconds,
            transport=self._transport,
        )

    async def status(self) -> LLMStatus:
        if not self._settings.llm_enabled:
            return LLMStatus(
                provider=self._settings.llm_provider,
                model=self._settings.llm_model,
                state=LLMState.DISABLED,
                manual_fallback=True,
                detail="로컬 LLM이 설정에서 비활성화되어 있습니다.",
            )

        if not self._settings.ollama_is_local:
            return LLMStatus(
                provider=self._settings.llm_provider,
                model=self._settings.llm_model,
                state=LLMState.UNAVAILABLE,
                manual_fallback=True,
                detail="개인정보 보호를 위해 로컬 Ollama 주소만 허용합니다.",
            )

        try:
            async with self._client() as client:
                response = await client.get("/api/tags")
                response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Ollama tags response must be an object")
        except (httpx.HTTPError, ValueError):
            return LLMStatus(
                provider=self._settings.llm_provider,
                model=self._settings.llm_model,
                state=LLMState.UNAVAILABLE,
                manual_fallback=True,
                detail="Ollama에 연결할 수 없어 수동 입력 모드로 전환합니다.",
            )

        installed_models = {
            model.get("name")
            for model in payload.get("models", [])
            if isinstance(model, dict)
        }
        if self._settings.llm_model not in installed_models:
            return LLMStatus(
                provider=self._settings.llm_provider,
                model=self._settings.llm_model,
                state=LLMState.MODEL_MISSING,
                manual_fallback=True,
                detail=f"{self._settings.llm_model} 모델이 설치되지 않았습니다.",
            )

        return LLMStatus(
            provider=self._settings.llm_provider,
            model=self._settings.llm_model,
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
        if not self._settings.ollama_is_local:
            raise ValueError("Only a loopback Ollama endpoint is allowed")
        request_body = {
            "model": self._settings.llm_model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {
                "num_ctx": self._settings.llm_context_tokens,
                "num_predict": self._settings.llm_max_output_tokens,
                "temperature": temperature,
            },
        }
        async with self._client() as client:
            response = await client.post("/api/chat", json=request_body)
            response.raise_for_status()

        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Ollama chat response must be an object")
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ValueError("Ollama chat response must contain a message")
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("Ollama message content must be text")
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("Ollama response must be a JSON object")
        return parsed
