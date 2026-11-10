"""The demo firms' registry data, replayed offline from the recordings in data/fixtures."""

import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from verikyb.config import RealFirm, load_demo_set
from verikyb.settings import Settings, SourceMode
from verikyb.sources.companies_house import CompaniesHouseClient
from verikyb.sources.http import RecordingStore, SourceUnavailableError

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "data" / "fixtures"
FIRMS = load_demo_set(ROOT / "config").real_firms


def comparable(name: str) -> str:
    """`Remitly U.K., Ltd` and `REMITLY U.K., LTD` are the same name."""
    return re.sub(r"[^a-z0-9]", "", name.casefold())


@pytest.fixture(params=FIRMS, ids=[firm.company_number for firm in FIRMS])
def firm(request: pytest.FixtureRequest) -> RealFirm:
    firm: RealFirm = request.param
    profile = httpx.URL(f"{CompaniesHouseClient.base_url}/company/{firm.company_number}")
    if not RecordingStore(FIXTURES / CompaniesHouseClient.name).path_for(profile).exists():
        pytest.skip(f"{firm.name} is not recorded yet: run `make record` with the API key set")
    return firm


@pytest.fixture
async def registry() -> AsyncIterator[CompaniesHouseClient]:
    client = CompaniesHouseClient(Settings(sources=SourceMode.REPLAY, fixtures_dir=FIXTURES))
    yield client
    await client.aclose()


@pytest.mark.anyio
async def test_the_profile_replays(firm: RealFirm, registry: CompaniesHouseClient) -> None:
    company = await registry.company(firm.company_number)

    assert company is not None
    assert company.number == firm.company_number
    assert company.jurisdiction == "GB"
    assert comparable(company.name) == comparable(firm.name)
    assert company.status == "active"
    assert company.incorporated_on is not None
    assert company.address is not None


@pytest.mark.anyio
async def test_a_search_by_name_finds_the_firm(firm: RealFirm, registry: CompaniesHouseClient) -> None:
    results = await registry.search(firm.name)

    assert firm.company_number in [company.number for company in results]


@pytest.mark.anyio
async def test_the_officers_replay(firm: RealFirm, registry: CompaniesHouseClient) -> None:
    officers = await registry.officers(firm.company_number)

    current = [officer for officer in officers if officer.resigned_on is None]
    assert any("director" in officer.role for officer in current), "no current director"
    assert all(officer.officer_id for officer in officers)


@pytest.mark.anyio
async def test_the_owners_replay(firm: RealFirm, registry: CompaniesHouseClient) -> None:
    owners = await registry.owners(firm.company_number)

    # Every demo firm is a subsidiary, so its parent is a current person with significant control.
    assert any(owner.ceased_on is None for owner in owners), "no current owner"
    for owner in owners:
        if owner.min_pct is not None and owner.max_pct is not None:
            assert owner.min_pct <= owner.max_pct


@pytest.mark.anyio
async def test_a_company_never_recorded_is_unavailable_not_fetched(
    registry: CompaniesHouseClient,
) -> None:
    with pytest.raises(SourceUnavailableError, match="no recorded response"):
        await registry.company("NOTRECRD")
