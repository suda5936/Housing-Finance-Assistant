from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from homefit_api.data import (
    DataRepository,
    InvalidSessionTokenError,
    SessionNotFoundError,
)
from homefit_api.data_api import get_data_repository
from homefit_api.document_api import get_document_service
from homefit_api.documents import DocumentNotFoundError, DocumentService
from homefit_api.orchestration import (
    AgentAdvanceRequest,
    AgentContextPatch,
    AgentOrchestrator,
    AgentRun,
    AgentRunCreate,
    AgentRunNotFoundError,
)
from homefit_api.policies import PolicyNotFoundError, PolicyRegistry
from homefit_api.rag import PolicyEvidenceRegistry

router = APIRouter(prefix="/sessions", tags=["agent-runs"])
SessionToken = Annotated[str, Header(alias="X-Session-Token", min_length=32)]
service = AgentOrchestrator(
    policies=PolicyRegistry.load_default(),
    evidence=PolicyEvidenceRegistry.load_default(),
)


def get_agent_orchestrator() -> AgentOrchestrator:
    return service


def _translate_agent_error(error: Exception) -> HTTPException:
    if isinstance(
        error,
        (SessionNotFoundError, AgentRunNotFoundError, DocumentNotFoundError, PolicyNotFoundError),
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    if isinstance(error, InvalidSessionTokenError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid session token")
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.post(
    "/{session_id}/agent-runs",
    response_model=AgentRun,
    status_code=status.HTTP_201_CREATED,
)
def create_agent_run(
    session_id: UUID,
    access_token: SessionToken,
    payload: AgentRunCreate,
    repository: Annotated[DataRepository, Depends(get_data_repository)],
    documents: Annotated[DocumentService, Depends(get_document_service)],
    orchestrator: Annotated[AgentOrchestrator, Depends(get_agent_orchestrator)],
) -> AgentRun:
    try:
        return orchestrator.create_run(repository, documents, session_id, access_token, payload)
    except Exception as error:
        raise _translate_agent_error(error) from error


@router.get("/{session_id}/agent-runs/{run_id}", response_model=AgentRun)
def get_agent_run(
    session_id: UUID,
    run_id: UUID,
    access_token: SessionToken,
    repository: Annotated[DataRepository, Depends(get_data_repository)],
    orchestrator: Annotated[AgentOrchestrator, Depends(get_agent_orchestrator)],
) -> AgentRun:
    try:
        return orchestrator.get_run(repository, session_id, access_token, run_id)
    except Exception as error:
        raise _translate_agent_error(error) from error


@router.put("/{session_id}/agent-runs/{run_id}/context", response_model=AgentRun)
def update_agent_context(
    session_id: UUID,
    run_id: UUID,
    access_token: SessionToken,
    payload: AgentContextPatch,
    repository: Annotated[DataRepository, Depends(get_data_repository)],
    documents: Annotated[DocumentService, Depends(get_document_service)],
    orchestrator: Annotated[AgentOrchestrator, Depends(get_agent_orchestrator)],
) -> AgentRun:
    try:
        return orchestrator.update_context(
            repository, documents, session_id, access_token, run_id, payload
        )
    except Exception as error:
        raise _translate_agent_error(error) from error


@router.post("/{session_id}/agent-runs/{run_id}/advance", response_model=AgentRun)
def advance_agent_run(
    session_id: UUID,
    run_id: UUID,
    access_token: SessionToken,
    payload: AgentAdvanceRequest,
    repository: Annotated[DataRepository, Depends(get_data_repository)],
    documents: Annotated[DocumentService, Depends(get_document_service)],
    orchestrator: Annotated[AgentOrchestrator, Depends(get_agent_orchestrator)],
) -> AgentRun:
    try:
        return orchestrator.advance(
            repository, documents, session_id, access_token, run_id, payload
        )
    except Exception as error:
        raise _translate_agent_error(error) from error
