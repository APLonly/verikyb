from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient
from flask.testing import FlaskClient

from verikyb import __version__
from verikyb.api.app import create_app as create_api
from verikyb.web.api_client import ApiClient
from verikyb.web.app import create_app as create_web

ONLINE = "Online"
UNAVAILABLE = "API unavailable"
REFUSED = "API refused the request"


def web_answered_by(handler: Callable[[httpx.Request], httpx.Response]) -> FlaskClient:
    http = httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))
    return create_web(api_client=ApiClient(http)).test_client()


def test_home_shows_online_and_the_version_when_the_api_answers() -> None:
    """Flask calls the real FastAPI app, in process instead of over the network."""
    web = create_web(api_client=ApiClient(TestClient(create_api()))).test_client()

    page = web.get("/")

    assert page.status_code == 200
    assert ONLINE in page.text
    assert f"API version {__version__}" in page.text
    assert UNAVAILABLE not in page.text


def test_home_shows_unavailable_when_the_api_cannot_be_reached() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    page = web_answered_by(refuse).get("/")

    assert page.status_code == 200  # the page itself still works
    assert UNAVAILABLE in page.text
    assert ONLINE not in page.text


@pytest.mark.parametrize("status", [500, 503])
def test_home_shows_unavailable_when_the_api_fails(status: int) -> None:
    page = web_answered_by(lambda _: httpx.Response(status)).get("/")

    assert page.status_code == 200
    assert UNAVAILABLE in page.text
    assert ONLINE not in page.text


def test_home_shows_the_apis_reason_when_it_refuses() -> None:
    reason = "Sign in <first>"  # also proves the reason is escaped, never run as HTML
    page = web_answered_by(lambda _: httpx.Response(401, json={"detail": reason})).get("/")

    assert page.status_code == 200
    assert REFUSED in page.text
    assert "Sign in &lt;first&gt;" in page.text
    assert ONLINE not in page.text
    assert UNAVAILABLE not in page.text


def test_a_failed_check_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    web_answered_by(lambda _: httpx.Response(503)).get("/")

    assert "API health check failed" in caplog.text
