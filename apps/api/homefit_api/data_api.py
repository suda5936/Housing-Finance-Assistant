from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from homefit_api.data import (
    CandidateDeleted,
    ConsentInput,
    ConsentRequiredError,
    DataRepository,
    DeletionReceipt,
    HousingCandidate,
    HousingCandidateInput,
    InMemoryDataRepository,
    InvalidSessionTokenError,
    SessionCreated,
    SessionExport,
    SessionNotFoundError,
    UserProfile,
    UserProfileInput,
)
from homefit_api.settings import get_settings

settings = get_settings()
repository = InMemoryDataRepository(
    upload_dir=settings.resolved_upload_dir,
    retention_hours=settings.document_retention_hours,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])
SessionToken = Annotated[str, Header(alias="X-Session-Token", min_length=32)]


def get_data_repository() -> DataRepository:
    return repository


def _translate_repository_error(error: Exception) -> HTTPException:
    if isinstance(error, SessionNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if isinstance(error, InvalidSessionTokenError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid session token")
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.post("", response_model=SessionCreated, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: ConsentInput,
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
) -> SessionCreated:
    try:
        return data_repository.create_session(payload)
    except ConsentRequiredError as error:
        raise _translate_repository_error(error) from error


@router.put("/{session_id}/profile", response_model=UserProfile)
def save_profile(
    session_id: UUID,
    access_token: SessionToken,
    payload: UserProfileInput,
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
) -> UserProfile:
    try:
        return data_repository.save_profile(session_id, access_token, payload)
    except (SessionNotFoundError, InvalidSessionTokenError, ValueError) as error:
        raise _translate_repository_error(error) from error


@router.post(
    "/{session_id}/candidates",
    response_model=HousingCandidate,
    status_code=status.HTTP_201_CREATED,
)
def add_candidate(
    session_id: UUID,
    access_token: SessionToken,
    payload: HousingCandidateInput,
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
) -> HousingCandidate:
    try:
        return data_repository.add_candidate(session_id, access_token, payload)
    except (SessionNotFoundError, InvalidSessionTokenError, ValueError) as error:
        raise _translate_repository_error(error) from error


@router.put("/{session_id}/candidates/{candidate_id}", response_model=HousingCandidate)
def update_candidate(
    session_id: UUID,
    candidate_id: UUID,
    access_token: SessionToken,
    payload: HousingCandidateInput,
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
) -> HousingCandidate:
    try:
        return data_repository.update_candidate(
            session_id, access_token, candidate_id, payload
        )
    except (SessionNotFoundError, InvalidSessionTokenError, ValueError) as error:
        raise _translate_repository_error(error) from error


@router.delete("/{session_id}/candidates/{candidate_id}", response_model=CandidateDeleted)
def delete_candidate(
    session_id: UUID,
    candidate_id: UUID,
    access_token: SessionToken,
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
) -> CandidateDeleted:
    try:
        return data_repository.delete_candidate(session_id, access_token, candidate_id)
    except (SessionNotFoundError, InvalidSessionTokenError, ValueError) as error:
        raise _translate_repository_error(error) from error


@router.get("/{session_id}/export", response_model=SessionExport)
def export_session(
    session_id: UUID,
    access_token: SessionToken,
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
) -> SessionExport:
    try:
        return data_repository.export_session(session_id, access_token)
    except (SessionNotFoundError, InvalidSessionTokenError) as error:
        raise _translate_repository_error(error) from error


@router.delete("/{session_id}", response_model=DeletionReceipt)
def delete_session(
    session_id: UUID,
    access_token: SessionToken,
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
) -> DeletionReceipt:
    try:
        return data_repository.delete_session(session_id, access_token)
    except (SessionNotFoundError, InvalidSessionTokenError) as error:
        raise _translate_repository_error(error) from error
