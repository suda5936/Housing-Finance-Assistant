from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status

from homefit_api.data import (
    DataRepository,
    InvalidSessionTokenError,
    SessionNotFoundError,
)
from homefit_api.data_api import get_data_repository
from homefit_api.documents import (
    ConfirmedDocumentFields,
    DocumentAnalysis,
    DocumentFieldName,
    DocumentNotFoundError,
    DocumentService,
    FieldReviewInput,
    UploadedDocument,
    UploadValidationError,
)
from homefit_api.settings import get_settings

settings = get_settings()
service = DocumentService(settings)
router = APIRouter(prefix="/sessions", tags=["documents"])
SessionToken = Annotated[str, Header(alias="X-Session-Token", min_length=32)]


def get_document_service() -> DocumentService:
    return service


def _translate_document_error(error: Exception) -> HTTPException:
    if isinstance(error, (SessionNotFoundError, DocumentNotFoundError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if isinstance(error, InvalidSessionTokenError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid session token")
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"{error} Manual candidate entry remains available.",
    )


@router.post(
    "/{session_id}/documents",
    response_model=UploadedDocument,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    session_id: UUID,
    access_token: SessionToken,
    file: Annotated[UploadFile, File()],
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
    document_service: Annotated[DocumentService, Depends(get_document_service)],
) -> UploadedDocument:
    content = await file.read(settings.document_max_bytes + 1)
    await file.close()
    try:
        return document_service.upload(
            data_repository,
            session_id,
            access_token,
            filename=file.filename or "",
            declared_media_type=file.content_type,
            content=content,
        )
    except (
        SessionNotFoundError,
        InvalidSessionTokenError,
        UploadValidationError,
        ValueError,
    ) as error:
        raise _translate_document_error(error) from error


@router.post(
    "/{session_id}/documents/{document_id}/extract",
    response_model=DocumentAnalysis,
)
def extract_document(
    session_id: UUID,
    document_id: UUID,
    access_token: SessionToken,
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
    document_service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentAnalysis:
    try:
        return document_service.extract(
            data_repository, session_id, access_token, document_id
        )
    except (
        SessionNotFoundError,
        InvalidSessionTokenError,
        DocumentNotFoundError,
        ValueError,
    ) as error:
        raise _translate_document_error(error) from error


@router.get(
    "/{session_id}/documents/{document_id}",
    response_model=DocumentAnalysis,
)
def get_document_analysis(
    session_id: UUID,
    document_id: UUID,
    access_token: SessionToken,
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
    document_service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentAnalysis:
    try:
        return document_service.get_analysis(
            data_repository, session_id, access_token, document_id
        )
    except (
        SessionNotFoundError,
        InvalidSessionTokenError,
        DocumentNotFoundError,
        ValueError,
    ) as error:
        raise _translate_document_error(error) from error


@router.put(
    "/{session_id}/documents/{document_id}/fields/{field_name}",
    response_model=DocumentAnalysis,
)
def review_document_field(
    session_id: UUID,
    document_id: UUID,
    field_name: DocumentFieldName,
    access_token: SessionToken,
    payload: FieldReviewInput,
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
    document_service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentAnalysis:
    try:
        return document_service.review_field(
            data_repository,
            session_id,
            access_token,
            document_id,
            field_name,
            payload,
        )
    except (
        SessionNotFoundError,
        InvalidSessionTokenError,
        DocumentNotFoundError,
        ValueError,
    ) as error:
        raise _translate_document_error(error) from error


@router.get(
    "/{session_id}/documents/{document_id}/confirmed-fields",
    response_model=ConfirmedDocumentFields,
)
def get_confirmed_document_fields(
    session_id: UUID,
    document_id: UUID,
    access_token: SessionToken,
    data_repository: Annotated[DataRepository, Depends(get_data_repository)],
    document_service: Annotated[DocumentService, Depends(get_document_service)],
) -> ConfirmedDocumentFields:
    try:
        return document_service.confirmed_fields(
            data_repository, session_id, access_token, document_id
        )
    except (SessionNotFoundError, InvalidSessionTokenError, DocumentNotFoundError) as error:
        raise _translate_document_error(error) from error
