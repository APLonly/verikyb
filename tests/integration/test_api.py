from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from verikyb import __version__
from verikyb.api.app import create_app


@pytest.fixture
def api() -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        yield client


def test_health_reports_status_mode_and_version(api: TestClient) -> None:
    response = api.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_health_is_documented_with_its_response_shape(api: TestClient) -> None:
    schema = api.get("/openapi.json").json()

    ok = schema["paths"]["/health"]["get"]["responses"]["200"]
    assert ok["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/HealthResponse"}
    assert set(schema["components"]["schemas"]["HealthResponse"]["required"]) == {
        "status",
        "version",
    }
    assert api.get("/docs").status_code == 200
