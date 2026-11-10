"""What every source client shares: a per-source rate limit, and recorded responses."""

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlencode

import httpx
from pydantic import AwareDatetime

from verikyb.models import DomainModel
from verikyb.settings import Settings, SourceMode

REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5
COMPACT_ABOVE = 512 * 1024
CREDENTIAL_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})


class SourceUnavailableError(Exception):
    """A source could not answer: nothing recorded in replay mode, or the API failed and there is no earlier recording to fall back on. Callers record "source unavailable" and go on."""


class RateLimiter:
    """At most `max_calls` in any `period` seconds, for every caller of one source."""

    def __init__(
        self,
        max_calls: int,
        period: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_calls < 1 or period <= 0:
            raise ValueError(f"a rate limit needs calls >= 1 and period > 0, not {max_calls}/{period}")
        self._max_calls = max_calls
        self._period = period
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until one more call fits in the window, then count it."""
        async with self._lock:  # concurrent callers take turns, so none can overtake the limit
            while True:
                now = self._clock()
                while self._calls and now - self._calls[0] >= self._period:
                    self._calls.popleft()
                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return
                await self._sleep(self._period - (now - self._calls[0]))


class Recording(DomainModel):
    url: str
    status: int
    content_type: str
    recorded_at: AwareDatetime
    synthetic: bool = False  # written by the synthetic data generator, not by a real API
    body: Any  # parsed JSON for JSON responses, so fixtures stay readable; text otherwise

    def to_response(self, request: httpx.Request) -> httpx.Response:
        content = self.body if isinstance(self.body, str) else json.dumps(self.body)
        headers = {"content-type": self.content_type}
        return httpx.Response(self.status, headers=headers, content=content, request=request)


class RecordingStore:
    """One JSON file per request URL, named after the path so a reviewer can find it.

    Request headers are not part of the key: each URL is fetched in one representation only.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def path_for(self, url: httpx.URL) -> Path:
        """The same request always maps to the same file, whatever the order of its query."""
        query = urlencode(sorted(url.params.multi_items()))
        key = f"{url.host}{url.path}?{query}"
        slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{url.path}?{query}").strip("-")[:80]
        return self._root / f"{slug}-{hashlib.sha256(key.encode()).hexdigest()[:10]}.json"

    def load(self, url: httpx.URL) -> Recording | None:
        path = self.path_for(url)
        if not path.exists():
            return None
        return Recording.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, recording: Recording) -> None:
        path = self.path_for(httpx.URL(recording.url))
        path.parent.mkdir(parents=True, exist_ok=True)
        data = recording.model_dump(mode="json")
        text = json.dumps(data, ensure_ascii=False)
        if len(text) < COMPACT_ABOVE:
            text = json.dumps(data, indent=2, ensure_ascii=False)
        handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
                file.write(text + "\n")
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


def _is_answer(status: int) -> bool:
    return 200 <= status < 300 or status == 404


def _body(response: httpx.Response) -> Any:
    """Parsed JSON when the source says JSON and means it; the text otherwise."""
    if "json" in response.headers.get("content-type", ""):
        try:
            parsed = response.json()
        except ValueError:
            return response.text
        if not isinstance(parsed, str):
            return parsed
    return response.text


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        store: RecordingStore,
        limiter: RateLimiter,
        mode: SourceMode,
        ttl: timedelta,
        network: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._store = store
        self._limiter = limiter
        self._mode = mode
        self._ttl = ttl
        self._network = network or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        recording = self._store.load(request.url)
        if self._mode is SourceMode.REPLAY:
            if recording is None:
                raise SourceUnavailableError(f"no recorded response for {request.url}")
            return recording.to_response(request)
        if recording and datetime.now(UTC) - recording.recorded_at < self._ttl:
            return recording.to_response(request)

        try:
            response = await self._fetch(request)
        except httpx.TransportError as exc:
            if recording:  # a stale answer beats no answer
                return recording.to_response(request)
            raise SourceUnavailableError(f"{request.url}: {exc!r}") from exc
        if not _is_answer(response.status_code):
            busy = response.status_code == 429 or response.status_code >= 500
            return recording.to_response(request) if recording and busy else response

        self._store.save(
            Recording(
                url=str(request.url),
                status=response.status_code,
                content_type=response.headers.get("content-type", ""),
                recorded_at=datetime.now(UTC),
                body=_body(response),
            )
        )
        return response

    async def _fetch(self, request: httpx.Request) -> httpx.Response:
        """Follows redirects here, so a recording holds the final answer for the URL the client asked for, never a short-lived signed download link. Every hop waits for the limiter."""
        for _ in range(MAX_REDIRECTS + 1):
            await self._limiter.acquire()
            response = await self._network.handle_async_request(request)
            await response.aread()
            if response.status_code not in REDIRECT_CODES:
                return response
            if "location" not in response.headers:
                raise SourceUnavailableError(f"{request.url} redirected without a location")
            request = _redirected(request, request.url.join(response.headers["location"]))
        raise SourceUnavailableError(f"too many redirects from {request.url}")

    async def aclose(self) -> None:
        await self._network.aclose()


def _redirected(request: httpx.Request, url: httpx.URL) -> httpx.Request:
    """The same GET at `url`, keeping headers such as a required User-Agent. Host is rebuilt for the new URL, and credentials never follow a redirect to another host."""
    dropped = {"host", "content-length"}
    if url.host != request.url.host:
        dropped |= CREDENTIAL_HEADERS
    headers = [(k, v) for k, v in request.headers.multi_items() if k.lower() not in dropped]
    return httpx.Request("GET", url, headers=headers)


def required_in_live_mode(settings: Settings, env_vars: str, *values: object) -> None:
    """Refuse to build a live client whose credentials are missing, naming what to set."""
    if settings.sources is SourceMode.LIVE and not all(values):
        raise ValueError(f"set {env_vars} to call this source live")


class SourceClient:
    """Base for one API: an httpx client wired to the recordings and the source's rate limit."""

    name: ClassVar[str]
    base_url: ClassVar[str]
    rate_limit: ClassVar[tuple[int, float]]

    def __init__(
        self,
        settings: Settings,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        network: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.limiter = RateLimiter(*self.rate_limit)
        transport = RecordingTransport(
            RecordingStore(settings.fixtures_dir / self.name),
            self.limiter,
            settings.sources,
            timedelta(hours=settings.cache_ttl_hours),
            network,
        )
        self._http = httpx.AsyncClient(base_url=self.base_url, headers=headers, auth=auth, transport=transport, timeout=30)

    async def _get(self, url: str, params: dict[str, str] | None = None, accept: str | None = None) -> httpx.Response | None:
        """None when the source says the thing does not exist (404): an answer, not a failure."""
        headers = {"accept": accept} if accept else None
        response = await self._http.get(url, params=params, headers=headers)
        if response.status_code == 404:
            return None
        if response.is_error:
            raise SourceUnavailableError(f"{self.name} answered {response.status_code}")
        return response

    async def _get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        response = await self._get(url, params)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise SourceUnavailableError(f"{self.name} did not answer with JSON") from exc

    async def aclose(self) -> None:
        await self._http.aclose()
