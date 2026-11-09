import pytest
from fastapi.testclient import TestClient
from flask import Flask
from flask.testing import FlaskClient
from pydantic import HttpUrl

from verikyb.api.app import create_app as create_api
from verikyb.settings import Settings, get_settings
from verikyb.web.api_client import ApiClient
from verikyb.web.app import SETTINGS_KEY, create_app

SETTINGS = Settings(api_url=HttpUrl("http://api.test:9000"), api_timeout_seconds=3)


@pytest.fixture
def web() -> FlaskClient:
    # The home page asks the API for its health: the real one, in process.
    return create_app(SETTINGS, api_client=ApiClient(TestClient(create_api()))).test_client()


def test_the_app_is_built_from_the_settings_it_is_given() -> None:
    assert create_app(SETTINGS).extensions[SETTINGS_KEY] is SETTINGS


def test_without_settings_the_app_reads_them_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VERIKYB_API_URL", "http://api.from-env.test:8100")
    get_settings.cache_clear()
    try:
        app: Flask = create_app()
    finally:
        get_settings.cache_clear()  # later tests must not see this environment

    assert str(app.extensions[SETTINGS_KEY].api_url) == "http://api.from-env.test:8100/"


def test_each_app_is_built_fresh() -> None:
    assert create_app(SETTINGS) is not create_app(SETTINGS)


def test_home_renders_inside_the_shared_layout(web: FlaskClient) -> None:
    page = web.get("/")

    assert page.status_code == 200
    assert page.mimetype == "text/html"
    assert "<h1" in page.text
    assert "KYB workbench" in page.text
    # From base.html, so every page that extends it has them.
    assert '<html lang="en">' in page.text
    assert "<title>VeriKYB</title>" in page.text
    assert 'href="#main">Skip to main content' in page.text
    assert '<main id="main"' in page.text
    assert 'href="/">VeriKYB</a>' in page.text
    assert "not legal or compliance advice" in page.text


def test_the_stylesheet_is_pinned_and_integrity_checked(web: FlaskClient) -> None:
    page = web.get("/").text

    assert "bootstrap@5.3.8/dist/css/bootstrap.min.css" in page
    assert 'integrity="sha384-' in page
    assert 'crossorigin="anonymous"' in page


def test_an_unknown_page_is_not_found(web: FlaskClient) -> None:
    assert web.get("/no-such-page").status_code == 404
