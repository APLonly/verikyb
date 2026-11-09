from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl, ValidationError

from verikyb import __version__
from verikyb.api.app import create_app as create_api
from verikyb.api.schemas import HealthResponse
from verikyb.settings import Settings
from verikyb.web.api_client import (
    ApiClient,
    ApiError,
    ApiRejectedError,
    ApiUnavailableError,
    get_api_client,
)
from verikyb.web.app import create_app as create_web

type Handler = Callable[[httpx.Request], httpx.Response]


def client_answering(handler: Handler) -> ApiClient:
    """A client whose every call is answered by `handler`, without a network."""
    return ApiClient(httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler)))


def test_health_parses_the_real_api_answer() -> None:
    """The real FastAPI app, called in process: this checks the contract between the two."""
    health = ApiClient(TestClient(create_api())).health()

    assert health == HealthResponse(status="ok", version=__version__)


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("connection refused"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.ReadTimeout("no answer in time"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
    ids=["refused", "connect-timeout", "read-timeout", "disconnected"],
)
def test_a_network_error_makes_the_api_unavailable(failure: httpx.HTTPError) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        failure.request = request
        raise failure

    with pytest.raises(ApiUnavailableError, match="GET /health failed") as raised:
        client_answering(fail).health()

    assert raised.value.__cause__ is failure  # kept for debugging


@pytest.mark.parametrize("status", [500, 502, 503, 504, 307])
def test_a_5xx_or_a_redirect_makes_the_api_unavailable(status: int) -> None:
    client = client_answering(lambda _: httpx.Response(status, json={"detail": "boom"}))

    with pytest.raises(ApiUnavailableError, match=f"answered {status}"):
        client.health()


def test_a_4xx_is_rejected_with_the_apis_reason() -> None:
    client = client_answering(lambda _: httpx.Response(403, json={"detail": "Not allowed here"}))

    with pytest.raises(ApiRejectedError) as raised:
        client.health()

    assert (raised.value.status_code, raised.value.reason) == (403, "Not allowed here")
    assert str(raised.value) == "403: Not allowed here"


def test_a_real_404_from_the_api_is_rejected_with_its_reason() -> None:
    client = ApiClient(TestClient(create_api()))

    with pytest.raises(ApiRejectedError) as raised:
        client._get("/no-such-route")

    assert (raised.value.status_code, raised.value.reason) == (404, "Not Found")


def test_a_validation_error_gives_each_field_message_as_the_reason() -> None:
    detail = [
        {"loc": ["body", "value"], "msg": "Field required", "type": "missing"},
        {"loc": ["body", "reason"], "msg": "String too short", "type": "string_too_short"},
    ]
    client = client_answering(lambda _: httpx.Response(422, json={"detail": detail}))

    with pytest.raises(ApiRejectedError) as raised:
        client.health()

    assert raised.value.reason == "Field required; String too short"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, text="<html>nginx: not found</html>"),
        httpx.Response(404, json=["not", "an", "object"]),
        httpx.Response(404, json={"detail": ""}),
        httpx.Response(404, json={"error": "a different shape"}),
    ],
    ids=["html", "json-list", "empty-detail", "no-detail"],
)
def test_a_4xx_without_a_reason_falls_back_to_the_status_phrase(response: httpx.Response) -> None:
    with pytest.raises(ApiRejectedError) as raised:
        client_answering(lambda _: response).health()

    assert raised.value.reason == "Not Found"


def test_pages_catch_both_errors_as_one_type() -> None:
    assert issubclass(ApiUnavailableError, ApiError)
    assert issubclass(ApiRejectedError, ApiError)


def test_an_answer_that_breaks_the_contract_fails_loudly() -> None:
    client = client_answering(lambda _: httpx.Response(200, json={"status": "maybe"}))

    with pytest.raises(ValidationError):
        client.health()


def test_from_settings_uses_the_api_url_and_timeout() -> None:
    settings = Settings(api_url=HttpUrl("http://api.example.test:9000"), api_timeout_seconds=3)

    http = ApiClient.from_settings(settings)._http

    assert str(http.base_url) == "http://api.example.test:9000/"
    assert http.timeout == httpx.Timeout(3)


def test_pages_get_the_client_the_app_was_built_with() -> None:
    client = client_answering(lambda _: httpx.Response(200))
    app = create_web(api_client=client)

    with app.app_context():
        assert get_api_client() is client


def test_without_a_client_the_app_builds_one_from_its_settings() -> None:
    settings = Settings(api_url=HttpUrl("http://api.example.test:9000"))
    app = create_web(settings)

    with app.app_context():
        assert str(get_api_client()._http.base_url) == "http://api.example.test:9000/"
