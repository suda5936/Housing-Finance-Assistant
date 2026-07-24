import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class Currency(StrEnum):
    KRW = "KRW"


class HouseholdType(StrEnum):
    SINGLE = "single"
    COUPLE = "couple"
    SINGLE_PARENT = "single_parent"
    OTHER = "other"


class Money(BaseModel):
    amount: NonNegativeMoney
    currency: Currency = Currency.KRW


class ConsentInput(BaseModel):
    consent_version: str = Field(min_length=1, max_length=30)
    privacy_notice_accepted: bool
    sensitive_data_notice_accepted: bool


class AnonymousSession(BaseModel):
    id: UUID
    created_at: datetime
    expires_at: datetime
    schema_version: str = "phase2-v1"


class SessionCreated(BaseModel):
    session: AnonymousSession
    access_token: str


class ConsentRecord(BaseModel):
    version: str
    privacy_notice_accepted: bool
    sensitive_data_notice_accepted: bool
    accepted_at: datetime


class UserProfileInput(BaseModel):
    age_years: int = Field(ge=19, le=100)
    monthly_net_income: Money
    liquid_assets: Money
    available_deposit: Money
    household_type: HouseholdType
    is_homeless: bool
    workplace_district: str | None = Field(default=None, max_length=50)


class UserProfile(UserProfileInput):
    id: UUID
    session_id: UUID
    input_version: str = "profile-v1"
    updated_at: datetime


class HousingCandidateInput(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    district: str = Field(min_length=1, max_length=50)
    deposit: Money
    monthly_rent: Money
    monthly_maintenance: Money | None = None
    area_sqm: Decimal = Field(gt=0, le=1000, max_digits=8, decimal_places=2)
    contract_months: int = Field(ge=1, le=120)
    commute_minutes_one_way: int | None = Field(default=None, ge=0, le=600)
    monthly_commute_cost: Money | None = None


class HousingCandidate(HousingCandidateInput):
    id: UUID
    session_id: UUID
    input_version: str = "candidate-v1"
    created_at: datetime


class CandidateDeleted(BaseModel):
    candidate_id: UUID
    deleted_at: datetime


class SourceDocument(BaseModel):
    id: UUID
    session_id: UUID
    original_filename: str
    sha256: str
    media_type: str
    expires_at: datetime
    masked: bool
    created_at: datetime


class ExtractedField(BaseModel):
    id: UUID
    document_id: UUID
    field_name: str
    extracted_value: str
    confirmed_value: str | None = None
    source_reference: str | None = None
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    confirmed_at: datetime | None = None
    extraction_version: str


class AnalysisSnapshot(BaseModel):
    id: UUID
    session_id: UUID
    input_payload: dict[str, object]
    input_sha256: str
    policy_version: str
    rule_version: str
    prompt_version: str
    model_version: str
    created_at: datetime


class SessionExport(BaseModel):
    session: AnonymousSession
    consent: ConsentRecord
    profile: UserProfile | None
    candidates: list[HousingCandidate]
    documents: list[SourceDocument]
    snapshots: list[AnalysisSnapshot]


class DeletionReceipt(BaseModel):
    session_id: UUID
    deleted_at: datetime
    database_records_deleted: int
    files_deleted: int
    cache_entries_deleted: int


class SessionNotFoundError(LookupError):
    pass


class InvalidSessionTokenError(PermissionError):
    pass


class ConsentRequiredError(ValueError):
    pass


@dataclass(slots=True)
class StoredDocument:
    metadata: SourceDocument
    storage_path: Path


@dataclass(slots=True)
class StoredSession:
    session: AnonymousSession
    access_token_hash: str
    consent: ConsentRecord
    profile: UserProfile | None = None
    candidates: dict[UUID, HousingCandidate] = field(default_factory=dict)
    documents: list[StoredDocument] = field(default_factory=list)
    snapshots: list[AnalysisSnapshot] = field(default_factory=list)


class DataRepository(Protocol):
    def register_cleanup_callback(self, callback: Callable[[UUID], int]) -> None: ...

    def authorize_session(self, session_id: UUID, access_token: str) -> None: ...

    def create_session(self, consent: ConsentInput) -> SessionCreated: ...

    def save_profile(
        self, session_id: UUID, access_token: str, payload: UserProfileInput
    ) -> UserProfile: ...

    def add_candidate(
        self, session_id: UUID, access_token: str, payload: HousingCandidateInput
    ) -> HousingCandidate: ...

    def update_candidate(
        self,
        session_id: UUID,
        access_token: str,
        candidate_id: UUID,
        payload: HousingCandidateInput,
    ) -> HousingCandidate: ...

    def delete_candidate(
        self, session_id: UUID, access_token: str, candidate_id: UUID
    ) -> CandidateDeleted: ...

    def register_document(
        self,
        session_id: UUID,
        access_token: str,
        *,
        metadata: SourceDocument,
        storage_path: Path,
    ) -> None: ...

    def export_session(self, session_id: UUID, access_token: str) -> SessionExport: ...

    def delete_session(self, session_id: UUID, access_token: str) -> DeletionReceipt: ...

    def purge_expired(self, *, now: datetime | None = None) -> int: ...


def snapshot_sha256(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InMemoryDataRepository:
    """Phase 2 repository used by local development and deterministic tests."""

    def __init__(self, *, upload_dir: Path, retention_hours: int = 24) -> None:
        self._upload_dir = upload_dir.resolve()
        self._retention_hours = retention_hours
        self._sessions: dict[UUID, StoredSession] = {}
        self._cleanup_callbacks: list[Callable[[UUID], int]] = []

    def register_cleanup_callback(self, callback: Callable[[UUID], int]) -> None:
        if callback not in self._cleanup_callbacks:
            self._cleanup_callbacks.append(callback)

    def _clear_session_caches(self, session_id: UUID) -> int:
        return sum(callback(session_id) for callback in self._cleanup_callbacks)

    @staticmethod
    def _hash_token(access_token: str) -> str:
        return hashlib.sha256(access_token.encode("utf-8")).hexdigest()

    def _authorized(self, session_id: UUID, access_token: str) -> StoredSession:
        stored = self._sessions.get(session_id)
        if stored is None:
            raise SessionNotFoundError(session_id)
        if stored.session.expires_at <= datetime.now(UTC):
            self._delete_registered_files(stored)
            self._clear_session_caches(session_id)
            del self._sessions[session_id]
            raise SessionNotFoundError(session_id)
        supplied_hash = self._hash_token(access_token)
        if not hmac.compare_digest(stored.access_token_hash, supplied_hash):
            raise InvalidSessionTokenError(session_id)
        return stored

    def create_session(self, consent: ConsentInput) -> SessionCreated:
        if not consent.privacy_notice_accepted or not consent.sensitive_data_notice_accepted:
            raise ConsentRequiredError("Both privacy notices must be accepted")

        now = datetime.now(UTC)
        access_token = secrets.token_urlsafe(32)
        session = AnonymousSession(
            id=uuid4(),
            created_at=now,
            expires_at=now + timedelta(hours=self._retention_hours),
        )
        consent_record = ConsentRecord(
            version=consent.consent_version,
            privacy_notice_accepted=True,
            sensitive_data_notice_accepted=True,
            accepted_at=now,
        )
        self._sessions[session.id] = StoredSession(
            session=session,
            access_token_hash=self._hash_token(access_token),
            consent=consent_record,
        )
        return SessionCreated(session=session, access_token=access_token)

    def authorize_session(self, session_id: UUID, access_token: str) -> None:
        self._authorized(session_id, access_token)

    def save_profile(
        self, session_id: UUID, access_token: str, payload: UserProfileInput
    ) -> UserProfile:
        stored = self._authorized(session_id, access_token)
        profile = UserProfile(
            **payload.model_dump(),
            id=stored.profile.id if stored.profile else uuid4(),
            session_id=session_id,
            updated_at=datetime.now(UTC),
        )
        stored.profile = profile
        return profile

    def add_candidate(
        self, session_id: UUID, access_token: str, payload: HousingCandidateInput
    ) -> HousingCandidate:
        stored = self._authorized(session_id, access_token)
        if len(stored.candidates) >= 3:
            raise ValueError("MVP allows up to three housing candidates")
        candidate = HousingCandidate(
            **payload.model_dump(),
            id=uuid4(),
            session_id=session_id,
            created_at=datetime.now(UTC),
        )
        stored.candidates[candidate.id] = candidate
        return candidate

    def update_candidate(
        self,
        session_id: UUID,
        access_token: str,
        candidate_id: UUID,
        payload: HousingCandidateInput,
    ) -> HousingCandidate:
        stored = self._authorized(session_id, access_token)
        existing = stored.candidates.get(candidate_id)
        if existing is None:
            raise ValueError("Housing candidate not found")
        candidate = HousingCandidate(
            **payload.model_dump(),
            id=candidate_id,
            session_id=session_id,
            created_at=existing.created_at,
        )
        stored.candidates[candidate_id] = candidate
        return candidate

    def delete_candidate(
        self, session_id: UUID, access_token: str, candidate_id: UUID
    ) -> CandidateDeleted:
        stored = self._authorized(session_id, access_token)
        if candidate_id not in stored.candidates:
            raise ValueError("Housing candidate not found")
        del stored.candidates[candidate_id]
        return CandidateDeleted(candidate_id=candidate_id, deleted_at=datetime.now(UTC))

    def export_session(self, session_id: UUID, access_token: str) -> SessionExport:
        stored = self._authorized(session_id, access_token)
        return SessionExport(
            session=stored.session,
            consent=stored.consent,
            profile=stored.profile,
            candidates=list(stored.candidates.values()),
            documents=[document.metadata for document in stored.documents],
            snapshots=list(stored.snapshots),
        )

    def register_document(
        self,
        session_id: UUID,
        access_token: str,
        *,
        metadata: SourceDocument,
        storage_path: Path,
    ) -> None:
        stored = self._authorized(session_id, access_token)
        if metadata.session_id != session_id:
            raise ValueError("Document session does not match")
        stored.documents.append(StoredDocument(metadata=metadata, storage_path=storage_path))

    def add_snapshot(
        self,
        session_id: UUID,
        access_token: str,
        *,
        input_payload: dict[str, object],
        policy_version: str,
        rule_version: str,
        prompt_version: str,
        model_version: str,
    ) -> AnalysisSnapshot:
        stored = self._authorized(session_id, access_token)
        snapshot = AnalysisSnapshot(
            id=uuid4(),
            session_id=session_id,
            input_payload=input_payload,
            input_sha256=snapshot_sha256(input_payload),
            policy_version=policy_version,
            rule_version=rule_version,
            prompt_version=prompt_version,
            model_version=model_version,
            created_at=datetime.now(UTC),
        )
        stored.snapshots.append(snapshot)
        return snapshot

    def _delete_registered_files(self, stored: StoredSession) -> int:
        files_deleted = 0
        for document in stored.documents:
            resolved_path = document.storage_path.resolve()
            if not resolved_path.is_relative_to(self._upload_dir):
                continue
            if resolved_path.is_file():
                resolved_path.unlink()
                files_deleted += 1
        return files_deleted

    def delete_session(self, session_id: UUID, access_token: str) -> DeletionReceipt:
        stored = self._authorized(session_id, access_token)
        files_deleted = self._delete_registered_files(stored)
        cache_entries_deleted = self._clear_session_caches(session_id)

        database_records_deleted = (
            2
            + int(stored.profile is not None)
            + len(stored.candidates)
            + len(stored.documents)
            + len(stored.snapshots)
        )
        del self._sessions[session_id]
        return DeletionReceipt(
            session_id=session_id,
            deleted_at=datetime.now(UTC),
            database_records_deleted=database_records_deleted,
            files_deleted=files_deleted,
            cache_entries_deleted=cache_entries_deleted,
        )

    def purge_expired(self, *, now: datetime | None = None) -> int:
        reference_time = now or datetime.now(UTC)
        expired = [
            stored
            for stored in self._sessions.values()
            if stored.session.expires_at <= reference_time
        ]
        for stored in expired:
            self._delete_registered_files(stored)
            self._clear_session_caches(stored.session.id)
            del self._sessions[stored.session.id]
        return len(expired)
