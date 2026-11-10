import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from verikyb.settings import Settings, SourceMode
from verikyb.sources.http import (
    RateLimiter,
    Recording,
    RecordingStore,
    RecordingTransport,
    SourceClient,
    SourceUnavailableError,
    required_in_live_mode,
)

BASE = "https://registry.example.test"
COMPANY_URL = f"{BASE}/company/01234567"
Handler = Callable[[httpx.Request], httpx.Response]


class Network:
    """A fake internet: answers with `handler` and remembers every request that reached it."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        self.transport = httpx.MockTransport(record)


class CountingLimiter(RateLimiter):
    def __init__(self) -> None:
        super().__init__(1000, 1)
        self.acquired = 0

    async def acquire(self) -> None:
        self.acquired += 1
        await super().acquire()


def recording(url: str = COMPANY_URL, body: object = None, age: timedelta = timedelta(0), status: int = 200) -> Recording:
    return Recording(
        url=url,
        status=status,
        content_type="application/json",
        recorded_at=datetime.now(UTC) - age,
        body=body if body is not None else {"company_name": "RECORDED LTD"},
    )


def client(
    store: RecordingStore,
    mode: SourceMode,
    network: Network | None = None,
    limiter: RateLimiter | None = None,
    ttl: timedelta = timedelta(hours=24),
) -> httpx.AsyncClient:
    transport = RecordingTransport(
        store,
        limiter or RateLimiter(1000, 1),
        mode,
        ttl,
        network.transport if network else None,
    )
    return httpx.AsyncClient(base_url=BASE, transport=transport)


def answering(body: object, status: int = 200) -> Network:
    return Network(lambda _: httpx.Response(status, json=body))


def unreachable() -> Network:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return Network(refuse)


@pytest.fixture
def store(tmp_path: Path) -> RecordingStore:
    return RecordingStore(tmp_path)


# Replay mode


@pytest.mark.anyio
async def test_replay_answers_from_the_recording(store: RecordingStore) -> None:
    store.save(recording())

    async with client(store, SourceMode.REPLAY) as http:
        response = await http.get("/company/01234567")

    assert response.status_code == 200
    assert response.json() == {"company_name": "RECORDED LTD"}


@pytest.mark.anyio
async def test_replay_never_touches_the_network(store: RecordingStore) -> None:
    store.save(recording())
    network = answering({"company_name": "LIVE LTD"})

    async with client(store, SourceMode.REPLAY, network) as http:
        await http.get("/company/01234567")
        with pytest.raises(SourceUnavailableError, match="no recorded response"):
            await http.get("/company/99999999")

    assert network.requests == []


@pytest.mark.anyio
async def test_replay_ignores_the_age_of_a_recording(store: RecordingStore) -> None:
    store.save(recording(age=timedelta(days=400)))

    async with client(store, SourceMode.REPLAY) as http:
        assert (await http.get("/company/01234567")).status_code == 200


@pytest.mark.anyio
async def test_a_recorded_404_replays_as_does_not_exist(store: RecordingStore) -> None:
    store.save(recording(status=404, body={"errors": [{"error": "company-profile-not-found"}]}))

    async with client(store, SourceMode.REPLAY) as http:
        assert (await http.get("/company/01234567")).status_code == 404


# Live mode


@pytest.mark.anyio
async def test_live_records_every_answer(store: RecordingStore) -> None:
    network = answering({"company_name": "LIVE LTD"})

    async with client(store, SourceMode.LIVE, network) as http:
        response = await http.get("/company/01234567")

    assert response.json() == {"company_name": "LIVE LTD"}
    saved = store.load(httpx.URL(COMPANY_URL))
    assert saved is not None
    assert (saved.status, saved.body, saved.url) == (200, {"company_name": "LIVE LTD"}, COMPANY_URL)
    assert saved.synthetic is False


@pytest.mark.anyio
async def test_live_records_a_404_too(store: RecordingStore) -> None:
    network = answering({"errors": [{"error": "company-profile-not-found"}]}, status=404)

    async with client(store, SourceMode.LIVE, network) as http:
        await http.get("/company/01234567")

    saved = store.load(httpx.URL(COMPANY_URL))
    assert saved is not None
    assert saved.status == 404


@pytest.mark.anyio
async def test_what_live_records_replays_the_same(store: RecordingStore) -> None:
    network = answering({"company_name": "LIVE LTD", "sic_codes": ["64999"]})

    async with client(store, SourceMode.LIVE, network) as http:
        live = await http.get("/company/01234567", params={"b": "2", "a": "1"})
    async with client(store, SourceMode.REPLAY) as http:
        replayed = await http.get("/company/01234567", params={"a": "1", "b": "2"})

    assert replayed.json() == live.json()


@pytest.mark.anyio
async def test_live_records_text_and_malformed_json_as_text(store: RecordingStore) -> None:
    pages = {
        "/accounts.xhtml": httpx.Response(200, text="<html>accounts</html>"),
        "/broken.json": httpx.Response(200, headers={"content-type": "application/json"}, text="{not json"),
    }
    network = Network(lambda request: pages[request.url.path])

    async with client(store, SourceMode.LIVE, network) as http:
        for path in pages:
            await http.get(path)
    async with client(store, SourceMode.REPLAY) as http:
        replayed = {path: (await http.get(path)).text for path in pages}

    assert replayed == {"/accounts.xhtml": "<html>accounts</html>", "/broken.json": "{not json"}


@pytest.mark.anyio
async def test_live_reuses_a_fresh_recording_without_calling_the_api(
    store: RecordingStore,
) -> None:
    store.save(recording(age=timedelta(hours=1)))
    network = answering({"company_name": "LIVE LTD"})

    async with client(store, SourceMode.LIVE, network) as http:
        response = await http.get("/company/01234567")

    assert response.json() == {"company_name": "RECORDED LTD"}
    assert network.requests == []


@pytest.mark.anyio
async def test_live_refreshes_a_stale_recording(store: RecordingStore) -> None:
    store.save(recording(age=timedelta(hours=25)))
    network = answering({"company_name": "LIVE LTD"})

    async with client(store, SourceMode.LIVE, network) as http:
        response = await http.get("/company/01234567")

    assert response.json() == {"company_name": "LIVE LTD"}
    saved = store.load(httpx.URL(COMPANY_URL))
    assert saved is not None
    assert saved.body == {"company_name": "LIVE LTD"}


@pytest.mark.anyio
async def test_live_falls_back_to_a_stale_recording_when_the_api_is_down(
    store: RecordingStore,
) -> None:
    store.save(recording(age=timedelta(days=30)))

    async with client(store, SourceMode.LIVE, unreachable()) as http:
        response = await http.get("/company/01234567")

    assert response.json() == {"company_name": "RECORDED LTD"}


@pytest.mark.anyio
async def test_live_without_network_or_recording_is_unavailable(store: RecordingStore) -> None:
    async with client(store, SourceMode.LIVE, unreachable()) as http:
        with pytest.raises(SourceUnavailableError, match="ConnectError"):
            await http.get("/company/01234567")


@pytest.mark.anyio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_busy_or_failing_api_never_overwrites_a_recording(store: RecordingStore, status: int) -> None:
    store.save(recording(age=timedelta(days=30)))

    async with client(store, SourceMode.LIVE, answering({"error": "busy"}, status)) as http:
        response = await http.get("/company/01234567")

    assert response.json() == {"company_name": "RECORDED LTD"}  # the older answer instead
    saved = store.load(httpx.URL(COMPANY_URL))
    assert saved is not None
    assert saved.body == {"company_name": "RECORDED LTD"}


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
async def test_a_refusal_or_failure_is_passed_on_and_never_recorded(store: RecordingStore, status: int) -> None:
    async with client(store, SourceMode.LIVE, answering({"error": "no"}, status)) as http:
        response = await http.get("/company/01234567")

    assert response.status_code == status
    assert store.load(httpx.URL(COMPANY_URL)) is None  # a bad key must not become a fixture


@pytest.mark.anyio
async def test_every_network_call_waits_for_the_rate_limiter(store: RecordingStore) -> None:
    limiter = CountingLimiter()
    store.save(recording(url=f"{BASE}/cached"))

    async with client(store, SourceMode.LIVE, answering({}), limiter) as http:
        await http.get("/cached")  # fresh recording: no call, no wait
        await http.get("/a")
        await http.get("/b")

    assert limiter.acquired == 2


# Redirects


def redirecting(hops: dict[str, str], final: httpx.Response) -> Network:
    def answer(request: httpx.Request) -> httpx.Response:
        target = hops.get(str(request.url))
        return httpx.Response(302, headers={"location": target}) if target else final

    return Network(answer)


@pytest.mark.anyio
async def test_a_redirect_is_followed_and_recorded_under_the_url_asked_for(
    store: RecordingStore,
) -> None:
    signed = "https://downloads.example.test/doc?signature=short-lived"
    network = redirecting({f"{BASE}/document/1": signed}, httpx.Response(200, text="pdf bytes"))
    limiter = CountingLimiter()

    async with client(store, SourceMode.LIVE, network, limiter) as http:
        response = await http.get("/document/1")

    assert response.text == "pdf bytes"
    saved = store.load(httpx.URL(f"{BASE}/document/1"))
    assert saved is not None
    assert saved.body == "pdf bytes"
    assert store.load(httpx.URL(signed)) is None  # the signed link is never kept
    assert limiter.acquired == 2  # each hop counts against the limit


@pytest.mark.anyio
async def test_credentials_never_follow_a_redirect_to_another_host(
    store: RecordingStore,
) -> None:
    hops = {f"{BASE}/a": f"{BASE}/b", f"{BASE}/b": "https://elsewhere.example.test/c"}
    network = redirecting(hops, httpx.Response(200, text="ok"))
    headers = {"authorization": "Basic c2VjcmV0", "user-agent": "VeriKYB admin@example.test"}

    async with client(store, SourceMode.LIVE, network) as http:
        await http.get("/a", headers=headers)

    same_host, other_host = network.requests[1], network.requests[2]
    assert same_host.headers["authorization"] == "Basic c2VjcmV0"
    assert "authorization" not in other_host.headers
    assert other_host.headers["user-agent"] == "VeriKYB admin@example.test"  # kept: SEC needs it
    assert other_host.headers["host"] == "elsewhere.example.test"


@pytest.mark.anyio
async def test_endless_redirects_make_the_source_unavailable(store: RecordingStore) -> None:
    network = Network(lambda r: httpx.Response(302, headers={"location": f"{r.url}x"}))

    async with client(store, SourceMode.LIVE, network) as http:
        with pytest.raises(SourceUnavailableError, match="too many redirects"):
            await http.get("/loop")

    assert len(network.requests) == 6  # the request and five redirects


@pytest.mark.anyio
async def test_a_redirect_without_a_location_makes_the_source_unavailable(
    store: RecordingStore,
) -> None:
    async with client(store, SourceMode.LIVE, Network(lambda _: httpx.Response(302))) as http:
        with pytest.raises(SourceUnavailableError, match="without a location"):
            await http.get("/nowhere")


# The store


def test_the_same_request_maps_to_the_same_file_whatever_the_query_order(
    store: RecordingStore,
) -> None:
    a = store.path_for(httpx.URL(f"{BASE}/search?q=wise&items=5"))
    b = store.path_for(httpx.URL(f"{BASE}/search?items=5&q=wise"))

    assert a == b
    assert a.name.startswith("search-items-5-q-wise-")


def test_similar_urls_never_share_a_file(store: RecordingStore) -> None:
    paths = {
        store.path_for(httpx.URL(url))
        for url in (
            f"{BASE}/company/1",
            f"{BASE}/company/1/",
            "https://other.example.test/company/1",
            f"{BASE}/company/1?x=1",
        )
    }
    assert len(paths) == 4


def test_a_recording_is_readable_json_with_lf_endings(store: RecordingStore) -> None:
    store.save(recording(body={"company_name": "ÉCLAIR LTD"}))

    raw = store.path_for(httpx.URL(COMPANY_URL)).read_bytes()

    assert b"\r\n" not in raw
    assert raw.endswith(b"}\n")
    assert "ÉCLAIR LTD" in raw.decode("utf-8")  # not escaped to É
    assert json.loads(raw)["body"] == {"company_name": "ÉCLAIR LTD"}
    assert raw.count(b"\n") > 1  # indented for review


def test_saving_leaves_no_temporary_files(store: RecordingStore, tmp_path: Path) -> None:
    store.save(recording())
    store.save(recording(body={"company_name": "UPDATED LTD"}))

    assert [p.suffix for p in tmp_path.iterdir()] == [".json"]


# The client base class


class Registry(SourceClient):
    name = "registry"
    base_url = BASE
    rate_limit: ClassVar[tuple[int, float]] = (600, 300.0)


def registry(tmp_path: Path, mode: SourceMode, network: Network | None = None) -> Registry:
    settings = Settings(sources=mode, fixtures_dir=tmp_path)
    return Registry(settings, network=network.transport if network else None)


@pytest.mark.anyio
async def test_each_source_replays_from_its_own_fixtures_folder(tmp_path: Path) -> None:
    RecordingStore(tmp_path / "registry").save(recording())
    source = registry(tmp_path, SourceMode.REPLAY)

    assert await source._get_json("/company/01234567") == {"company_name": "RECORDED LTD"}
    await source.aclose()


@pytest.mark.anyio
async def test_a_source_records_into_its_own_fixtures_folder(tmp_path: Path) -> None:
    source = registry(tmp_path, SourceMode.LIVE, answering({"company_name": "LIVE LTD"}))

    await source._get_json("/company/01234567")
    await source.aclose()

    assert RecordingStore(tmp_path / "registry").load(httpx.URL(COMPANY_URL)) is not None


@pytest.mark.anyio
async def test_does_not_exist_is_an_answer_not_a_failure(tmp_path: Path) -> None:
    source = registry(tmp_path, SourceMode.LIVE, answering({}, status=404))

    assert await source._get("/company/00000000") is None
    assert await source._get_json("/company/00000000") is None
    await source.aclose()


@pytest.mark.anyio
async def test_an_error_status_makes_the_source_unavailable(tmp_path: Path) -> None:
    source = registry(tmp_path, SourceMode.LIVE, answering({}, status=401))

    with pytest.raises(SourceUnavailableError, match="registry answered 401"):
        await source._get("/company/01234567")
    await source.aclose()


@pytest.mark.anyio
async def test_an_answer_that_is_not_json_makes_the_source_unavailable(tmp_path: Path) -> None:
    network = Network(lambda _: httpx.Response(200, text="<html>maintenance</html>"))
    source = registry(tmp_path, SourceMode.LIVE, network)

    with pytest.raises(SourceUnavailableError, match="did not answer with JSON"):
        await source._get_json("/company/01234567")
    await source.aclose()


def test_live_mode_refuses_to_start_without_credentials() -> None:
    live, replay = Settings(sources=SourceMode.LIVE), Settings(sources=SourceMode.REPLAY)

    with pytest.raises(ValueError, match="set VERIKYB_REGISTRY_KEY"):
        required_in_live_mode(live, "VERIKYB_REGISTRY_KEY", None)
    required_in_live_mode(live, "VERIKYB_REGISTRY_KEY", "a-key")
    required_in_live_mode(replay, "VERIKYB_REGISTRY_KEY", None)  # replay needs no key


def test_sources_replay_by_default() -> None:
    assert Settings().sources is SourceMode.REPLAY
