from fastapi.testclient import TestClient

from homefit_api.main import app


def test_health_returns_safe_service_metadata() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "homefit-ai",
        "environment": "development",
    }
