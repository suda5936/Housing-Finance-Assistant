from fastapi.testclient import TestClient

from homefit_api.main import app
from homefit_api.observability import metrics


def test_request_id_and_security_headers_are_returned() -> None:
    response = TestClient(app).get(
        "/health", headers={"X-Request-ID": "phase10-test-request"}
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "phase10-test-request"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "geolocation=()" in response.headers["permissions-policy"]


def test_invalid_request_id_is_replaced_and_error_can_be_correlated() -> None:
    response = TestClient(app).get(
        "/sessions/not-a-uuid/export",
        headers={"X-Request-ID": "contains spaces and secrets"},
    )

    request_id = response.headers["x-request-id"]
    assert response.status_code == 422
    assert request_id != "contains spaces and secrets"
    assert response.json()["error"]["request_id"] == request_id
    assert response.headers["cache-control"] == "no-store"


def test_oversized_request_is_rejected_before_body_processing() -> None:
    response = TestClient(app).post(
        "/sessions",
        content=b"{}",
        headers={"Content-Length": str(20 * 1024 * 1024)},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_metrics_use_route_templates_without_user_identifiers() -> None:
    metrics.reset()
    client = TestClient(app)
    client.get("/health")
    client.get("/sessions/not-a-uuid/export")

    response = client.get("/system/metrics")
    snapshot = response.json()
    routes = {item["route"]: item for item in snapshot["routes"]}

    assert response.status_code == 200
    assert routes["/health"]["requests"] == 1
    assert routes["/sessions/{session_id}/export"]["errors"] == 1
    assert "not-a-uuid" not in response.text


def test_untrusted_host_is_rejected() -> None:
    response = TestClient(app).get("/health", headers={"Host": "attacker.example"})

    assert response.status_code == 400
