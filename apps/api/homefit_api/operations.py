import json
import os
import sys
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

from homefit_api.evaluation import evaluate_release
from homefit_api.llm import LanguageModelGateway, LLMState
from homefit_api.policies import PolicyRegistry
from homefit_api.settings import Settings


class OperationalState(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class OperationalCheck(BaseModel):
    code: str
    state: OperationalState
    required: bool
    detail: str


class ReadinessReport(BaseModel):
    status: OperationalState
    service: str
    environment: str
    checks: list[OperationalCheck]


class EnvironmentReport(BaseModel):
    environment: str
    checks: list[OperationalCheck]
    blockers: list[str]
    warnings: list[str]
    deployable: bool


def _storage_check(path: Path) -> OperationalCheck:
    existing = path
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    writable = existing.is_dir() and os.access(existing, os.W_OK)
    return OperationalCheck(
        code="storage_writable",
        state=OperationalState.READY if writable else OperationalState.BLOCKED,
        required=True,
        detail=(
            "업로드 경로를 생성·정리할 수 있습니다."
            if writable
            else "업로드 경로의 상위 디렉터리에 쓰기 권한이 없습니다."
        ),
    )


async def evaluate_readiness(
    settings: Settings, gateway: LanguageModelGateway
) -> ReadinessReport:
    checks = [_storage_check(settings.resolved_upload_dir)]
    try:
        policies = PolicyRegistry.load_default().list_policies()
        reviewed = sum(item.activatable for item in policies)
        checks.append(
            OperationalCheck(
                code="policy_catalog",
                state=(
                    OperationalState.READY
                    if reviewed == len(policies)
                    else OperationalState.DEGRADED
                ),
                required=True,
                detail=f"정책 {len(policies)}개 로드, 독립 검토 완료 {reviewed}개",
            )
        )
    except (OSError, ValueError) as error:
        checks.append(
            OperationalCheck(
                code="policy_catalog",
                state=OperationalState.BLOCKED,
                required=True,
                detail=f"정책 카탈로그를 검증할 수 없습니다: {type(error).__name__}",
            )
        )

    repository_state = (
        OperationalState.DEGRADED
        if settings.data_repository == "memory"
        else OperationalState.BLOCKED
    )
    checks.append(
        OperationalCheck(
            code="data_repository",
            state=repository_state,
            required=True,
            detail=(
                "단일 프로세스 데모용 메모리 저장소입니다."
                if settings.data_repository == "memory"
                else "설정된 영속 저장소 어댑터가 아직 구현되지 않았습니다."
            ),
        )
    )

    llm_status = await gateway.status()
    checks.append(
        OperationalCheck(
            code="local_llm",
            state=(
                OperationalState.READY
                if llm_status.state is LLMState.READY
                else OperationalState.DEGRADED
            ),
            required=False,
            detail=llm_status.detail,
        )
    )
    required_blocked = any(
        item.required and item.state is OperationalState.BLOCKED for item in checks
    )
    degraded = any(item.state is OperationalState.DEGRADED for item in checks)
    status = (
        OperationalState.BLOCKED
        if required_blocked
        else OperationalState.DEGRADED
        if degraded
        else OperationalState.READY
    )
    return ReadinessReport(
        status=status,
        service=settings.app_name,
        environment=settings.app_env,
        checks=checks,
    )


def validate_environment(settings: Settings) -> EnvironmentReport:
    checks: list[OperationalCheck] = []

    def add(code: str, state: OperationalState, detail: str, *, required: bool = True) -> None:
        checks.append(
            OperationalCheck(code=code, state=state, required=required, detail=detail)
        )

    known_environment = settings.app_env in {"development", "staging", "production"}
    add(
        "known_environment",
        OperationalState.READY if known_environment else OperationalState.BLOCKED,
        f"APP_ENV={settings.app_env}",
    )
    add(
        "cors_allowlist",
        OperationalState.BLOCKED
        if "*" in settings.cors_origin_list
        else OperationalState.READY,
        "CORS는 명시된 origin만 허용합니다."
        if "*" not in settings.cors_origin_list
        else "와일드카드 CORS는 허용되지 않습니다.",
    )
    add(
        "trusted_hosts",
        OperationalState.BLOCKED
        if "*" in settings.trusted_host_list
        else OperationalState.READY,
        "Host 허용 목록이 명시되어 있습니다."
        if "*" not in settings.trusted_host_list
        else "와일드카드 Host는 허용되지 않습니다.",
    )
    add(
        "request_size",
        OperationalState.READY
        if settings.max_request_bytes > settings.document_max_bytes
        else OperationalState.BLOCKED,
        "multipart 여유를 포함한 요청 한도가 문서 한도보다 큽니다.",
    )
    add(
        "local_llm_boundary",
        OperationalState.READY
        if not settings.llm_enabled or settings.ollama_is_local
        else OperationalState.BLOCKED,
        "LLM은 비활성화되었거나 loopback Ollama만 사용합니다.",
    )

    production = settings.app_env == "production"
    add(
        "https_hsts",
        OperationalState.READY
        if not production or settings.enable_hsts
        else OperationalState.BLOCKED,
        "프로덕션 HSTS가 활성화되어 있습니다."
        if production and settings.enable_hsts
        else "로컬 HTTP에서는 HSTS를 사용하지 않습니다."
        if not production
        else "프로덕션은 ENABLE_HSTS=true가 필요합니다.",
    )
    add(
        "persistent_repository",
        OperationalState.BLOCKED
        if production
        else OperationalState.DEGRADED,
        "PostgreSQL 저장소 어댑터가 없어 공개 프로덕션 배포를 차단합니다."
        if production
        else "메모리 저장소는 단일 프로세스 데모 전용입니다.",
    )
    database_password = urlparse(settings.database_url).password or ""
    default_secret = database_password == "local_homefit_password"
    add(
        "database_secret",
        OperationalState.BLOCKED
        if production and default_secret
        else OperationalState.READY
        if not default_secret
        else OperationalState.DEGRADED,
        "기본 로컬 DB 비밀번호를 프로덕션에서 사용할 수 없습니다."
        if default_secret
        else "기본 예시와 다른 DB 자격정보가 설정됐습니다.",
    )

    release = evaluate_release()
    add(
        "automated_release_gate",
        OperationalState.READY
        if release.automated_gate_passed
        else OperationalState.BLOCKED,
        f"자동 차단 이슈 {len(release.automated_blockers)}건",
    )
    add(
        "manual_release_gates",
        OperationalState.DEGRADED
        if release.pending_manual_gates
        else OperationalState.READY,
        f"증거 대기 수동 게이트 {len(release.pending_manual_gates)}건",
    )
    blockers = [item.code for item in checks if item.state is OperationalState.BLOCKED]
    warnings = [item.code for item in checks if item.state is OperationalState.DEGRADED]
    return EnvironmentReport(
        environment=settings.app_env,
        checks=checks,
        blockers=blockers,
        warnings=warnings,
        deployable=not blockers,
    )


def main() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    report = validate_environment(Settings())
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    if report.blockers:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
