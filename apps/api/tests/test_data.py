from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from homefit_api.data import (
    ConsentInput,
    InMemoryDataRepository,
    SourceDocument,
    snapshot_sha256,
)
from homefit_api.data_api import get_data_repository
from homefit_api.main import app


def _create_session(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/sessions",
        json={
            "consent_version": "privacy-v1",
            "privacy_notice_accepted": True,
            "sensitive_data_notice_accepted": True,
        },
    )
    assert response.status_code == 201
    body = response.json()
    return body["session"]["id"], body["access_token"]


def _profile_payload() -> dict[str, object]:
    return {
        "age_years": 27,
        "monthly_net_income": {"amount": "2500000", "currency": "KRW"},
        "liquid_assets": {"amount": "15000000", "currency": "KRW"},
        "available_deposit": {"amount": "10000000", "currency": "KRW"},
        "household_type": "single",
        "is_homeless": True,
        "workplace_district": "서울 중구",
    }


def _candidate_payload(label: str = "A주택") -> dict[str, object]:
    return {
        "label": label,
        "district": "서울 마포구",
        "deposit": {"amount": "10000000", "currency": "KRW"},
        "monthly_rent": {"amount": "550000", "currency": "KRW"},
        "monthly_maintenance": {"amount": "70000", "currency": "KRW"},
        "area_sqm": "24.50",
        "contract_months": 12,
        "commute_minutes_one_way": 35,
        "monthly_commute_cost": {"amount": "62000", "currency": "KRW"},
    }


def test_session_profile_candidate_export_and_delete(tmp_path: Path) -> None:
    repository = InMemoryDataRepository(upload_dir=tmp_path)
    app.dependency_overrides[get_data_repository] = lambda: repository
    try:
        client = TestClient(app)
        session_id, token = _create_session(client)
        headers = {"X-Session-Token": token}

        profile_response = client.put(
            f"/sessions/{session_id}/profile",
            headers=headers,
            json=_profile_payload(),
        )
        candidate_response = client.post(
            f"/sessions/{session_id}/candidates",
            headers=headers,
            json=_candidate_payload(),
        )
        export_response = client.get(f"/sessions/{session_id}/export", headers=headers)

        assert profile_response.status_code == 200
        assert candidate_response.status_code == 201
        assert export_response.status_code == 200
        assert export_response.json()["profile"]["input_version"] == "profile-v1"
        assert len(export_response.json()["candidates"]) == 1
        assert "access_token" not in export_response.text

        delete_response = client.delete(f"/sessions/{session_id}", headers=headers)
        after_delete = client.get(f"/sessions/{session_id}/export", headers=headers)

        assert delete_response.status_code == 200
        assert delete_response.json()["database_records_deleted"] == 4
        assert after_delete.status_code == 404
        assert after_delete.json()["error"]["code"] == "NOT_FOUND"
    finally:
        app.dependency_overrides.clear()


def test_session_rejects_invalid_token(tmp_path: Path) -> None:
    repository = InMemoryDataRepository(upload_dir=tmp_path)
    app.dependency_overrides[get_data_repository] = lambda: repository
    try:
        client = TestClient(app)
        session_id, _ = _create_session(client)
        response = client.get(
            f"/sessions/{session_id}/export",
            headers={"X-Session-Token": "x" * 32},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_candidate_can_be_updated_and_deleted_for_ui_recalculation(tmp_path: Path) -> None:
    repository = InMemoryDataRepository(upload_dir=tmp_path)
    app.dependency_overrides[get_data_repository] = lambda: repository
    try:
        client = TestClient(app)
        session_id, token = _create_session(client)
        headers = {"X-Session-Token": token}
        created = client.post(
            f"/sessions/{session_id}/candidates",
            headers=headers,
            json=_candidate_payload(),
        )
        candidate_id = created.json()["id"]
        updated_payload = _candidate_payload("수정한 후보")
        updated_payload["monthly_rent"] = {"amount": "500000", "currency": "KRW"}
        updated = client.put(
            f"/sessions/{session_id}/candidates/{candidate_id}",
            headers=headers,
            json=updated_payload,
        )
        deleted = client.delete(
            f"/sessions/{session_id}/candidates/{candidate_id}",
            headers=headers,
        )
        exported = client.get(f"/sessions/{session_id}/export", headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["label"] == "수정한 후보"
    assert updated.json()["monthly_rent"]["amount"] == "500000"
    assert deleted.status_code == 200
    assert deleted.json()["candidate_id"] == candidate_id
    assert exported.json()["candidates"] == []


def test_validation_rejects_underage_profile(tmp_path: Path) -> None:
    repository = InMemoryDataRepository(upload_dir=tmp_path)
    app.dependency_overrides[get_data_repository] = lambda: repository
    try:
        client = TestClient(app)
        session_id, token = _create_session(client)
        payload = _profile_payload()
        payload["age_years"] = 18
        response = client.put(
            f"/sessions/{session_id}/profile",
            headers={"X-Session-Token": token},
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["fields"][0]["field"] == "age_years"


def test_delete_removes_only_registered_files_inside_upload_root(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    inside_file = upload_dir / "document.txt"
    inside_file.write_text("synthetic document", encoding="utf-8")
    outside_file = tmp_path / "keep.txt"
    outside_file.write_text("keep", encoding="utf-8")

    repository = InMemoryDataRepository(upload_dir=upload_dir)
    created = repository.create_session(
        ConsentInput(
            consent_version="privacy-v1",
            privacy_notice_accepted=True,
            sensitive_data_notice_accepted=True,
        )
    )
    metadata = SourceDocument(
        id=uuid4(),
        session_id=created.session.id,
        original_filename="synthetic.txt",
        sha256="a" * 64,
        media_type="text/plain",
        expires_at=created.session.expires_at,
        masked=True,
        created_at=datetime.now(UTC),
    )
    repository.register_document(
        created.session.id,
        created.access_token,
        metadata=metadata,
        storage_path=inside_file,
    )
    repository.register_document(
        created.session.id,
        created.access_token,
        metadata=metadata.model_copy(update={"id": uuid4()}),
        storage_path=outside_file,
    )

    receipt = repository.delete_session(created.session.id, created.access_token)

    assert receipt.files_deleted == 1
    assert not inside_file.exists()
    assert outside_file.exists()


def test_snapshot_hash_is_deterministic() -> None:
    first = {"income": 2500000, "rate": 1.5, "versions": {"rule": "v1", "policy": "2026"}}
    second = {"versions": {"policy": "2026", "rule": "v1"}, "rate": 1.5, "income": 2500000}

    assert snapshot_sha256(first) == snapshot_sha256(second)


def test_declined_consent_is_rejected(tmp_path: Path) -> None:
    repository = InMemoryDataRepository(upload_dir=tmp_path)
    app.dependency_overrides[get_data_repository] = lambda: repository
    try:
        response = TestClient(app).post(
            "/sessions",
            json={
                "consent_version": "privacy-v1",
                "privacy_notice_accepted": False,
                "sensitive_data_notice_accepted": True,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"


def test_retention_purge_removes_expired_session(tmp_path: Path) -> None:
    repository = InMemoryDataRepository(upload_dir=tmp_path, retention_hours=0)
    created = repository.create_session(
        ConsentInput(
            consent_version="privacy-v1",
            privacy_notice_accepted=True,
            sensitive_data_notice_accepted=True,
        )
    )

    purged = repository.purge_expired(now=datetime.now(UTC) + timedelta(seconds=1))

    assert purged == 1
    try:
        repository.export_session(created.session.id, created.access_token)
    except LookupError:
        pass
    else:
        raise AssertionError("Expired session must be removed")


def test_expired_session_is_blocked_before_scheduled_purge(tmp_path: Path) -> None:
    repository = InMemoryDataRepository(upload_dir=tmp_path, retention_hours=0)
    created = repository.create_session(
        ConsentInput(
            consent_version="privacy-v1",
            privacy_notice_accepted=True,
            sensitive_data_notice_accepted=True,
        )
    )

    try:
        repository.export_session(created.session.id, created.access_token)
    except LookupError:
        pass
    else:
        raise AssertionError("Expired session must not remain accessible")
