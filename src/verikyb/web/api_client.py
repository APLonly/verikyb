"""HTTP client for the FastAPI service."""

from typing import Any, Self, cast

import httpx
from flask import current_app

from verikyb.api.schemas import HealthResponse
from verikyb.settings import Settings

EXTENSION_KEY = "verikyb_api"


class ApiError(Exception):
    """Base of the two errors a page catches."""


class ApiUnavailableError(ApiError):
    """The API could not be reached or failed on its side; pages render an error state."""


class ApiRejectedError(ApiError):
    """The API refused the request (a 4xx); `reason` is the API's own explanation."""

    def __init__(self, status_code: int, reason: str) -> None:
        super().__init__(f"{status_code}: {reason}")
        self.status_code = status_code
        self.reason = reason


class ApiClient:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(httpx.Client(base_url=str(settings.api_url), timeout=settings.api_timeout_seconds))

    def health(self) -> HealthResponse:
        return HealthResponse.model_validate_json(self._get("/health").content)

    def _get(self, path: str) -> httpx.Response:
        try:
            response = self._http.get(path)
        except httpx.HTTPError as exc:
            raise ApiUnavailableError(f"GET {path} failed: {exc}") from exc
        if response.is_success:
            return response
        if response.is_client_error:
            raise ApiRejectedError(response.status_code, _reason(response))
        # A 5xx, or a redirect: the API is down or the URL points somewhere else.
        raise ApiUnavailableError(f"GET {path} answered {response.status_code}")


def _reason(response: httpx.Response) -> str:
    """FastAPI puts its reason in `detail`: a sentence, or a list of field errors for a 422."""
    try:
        detail: Any = response.json().get("detail")
    except ValueError, AttributeError:
        detail = None
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(detail, list) and detail:
        return "; ".join(str(error.get("msg", error)) for error in detail)
    return response.reason_phrase or f"HTTP {response.status_code}"


def get_api_client() -> ApiClient:
    """The client that `create_app` registered on the current Flask app."""
    return cast(ApiClient, current_app.extensions[EXTENSION_KEY])
