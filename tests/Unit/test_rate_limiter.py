import asyncio
from pathlib import Path
from typing import ClassVar

import pytest

from verikyb.settings import Settings
from verikyb.sources.http import RateLimiter, SourceClient


class FakeClock:
    """Time that moves only when someone sleeps, so a test of minutes runs instantly."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)  # let other waiting callers run, as a real sleep would


def limiter(max_calls: int, period: float, clock: FakeClock) -> RateLimiter:
    return RateLimiter(max_calls, period, clock=clock, sleep=clock.sleep)


@pytest.mark.anyio
async def test_rate_limiter_never_exceeds_the_source_limit() -> None:
    clock = FakeClock()
    two_per_ten_seconds = limiter(2, 10, clock)

    calls = []
    for _ in range(5):
        await two_per_ten_seconds.acquire()
        calls.append(clock.now)

    assert calls == [0, 0, 10, 10, 20]


@pytest.mark.anyio
async def test_the_window_slides_rather_than_resetting() -> None:
    clock = FakeClock()
    two_per_ten_seconds = limiter(2, 10, clock)

    await two_per_ten_seconds.acquire()  # at 0
    clock.now = 6
    await two_per_ten_seconds.acquire()  # at 6: two calls in the window
    await two_per_ten_seconds.acquire()  # waits for the call at 0 to leave the window: 10

    assert clock.now == 10


@pytest.mark.anyio
async def test_calls_spaced_out_by_the_caller_never_wait() -> None:
    clock = FakeClock()
    one_per_second = limiter(1, 1, clock)

    for second in range(5):
        clock.now = float(second)
        await one_per_second.acquire()

    assert clock.now == 4  # no call had to sleep


@pytest.mark.anyio
async def test_concurrent_callers_share_one_limit() -> None:
    clock = FakeClock()
    two_per_ten_seconds = limiter(2, 10, clock)
    started: list[float] = []

    async def call() -> None:
        await two_per_ten_seconds.acquire()
        started.append(clock.now)

    await asyncio.gather(*(call() for _ in range(6)))

    assert sorted(started) == [0, 0, 10, 10, 20, 20]


@pytest.mark.parametrize(("max_calls", "period"), [(0, 10), (-1, 10), (2, 0), (2, -5)])
def test_an_impossible_limit_is_refused(max_calls: int, period: float) -> None:
    with pytest.raises(ValueError, match="rate limit"):
        RateLimiter(max_calls, period)


class SlowSource(SourceClient):
    name = "slow"
    base_url = "https://slow.example.test"
    rate_limit: ClassVar[tuple[int, float]] = (2, 10.0)


class FastSource(SourceClient):
    name = "fast"
    base_url = "https://fast.example.test"
    rate_limit: ClassVar[tuple[int, float]] = (600, 300.0)


@pytest.mark.anyio
async def test_each_source_has_its_own_rate_limit(tmp_path: Path) -> None:
    settings = Settings(fixtures_dir=tmp_path)
    slow, fast = SlowSource(settings), FastSource(settings)
    clock = FakeClock()
    for client in (slow, fast):
        client.limiter._clock, client.limiter._sleep = clock, clock.sleep

    for _ in range(2):
        await slow.limiter.acquire()
    for _ in range(100):
        await fast.limiter.acquire()  # the slow source's full window does not hold it up

    assert clock.now == 0
    await slow.limiter.acquire()
    assert clock.now == 10  # only the slow source waits
    await asyncio.gather(slow.aclose(), fast.aclose())
