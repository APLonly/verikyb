"""The synthetic world, replayed offline from what `data/synthetic/generate.py` wrote: its companies answer like real ones, a figure a company did not file stays missing, and every demo company has its expected outcome in `ground_truth.json`."""

import json
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest
import yaml

from verikyb.finance.provider import CompaniesHouseFinancials
from verikyb.settings import Settings, SourceMode
from verikyb.sources.companies_house import CompaniesHouseClient
from verikyb.sources.http import SourceUnavailableError

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = ROOT / "data" / "synthetic"


@pytest.fixture
async def registry() -> AsyncIterator[CompaniesHouseClient]:
    replay = Settings(sources=SourceMode.REPLAY, fixtures_dir=ROOT / "data" / "fixtures")
    client = CompaniesHouseClient(replay)
    yield client
    await client.aclose()


@pytest.mark.anyio
async def test_companies_house_answers_from_recordings_only(registry: CompaniesHouseClient) -> None:
    company = await registry.company("99000105")
    owners = await registry.owners("99000101")

    assert company is not None
    assert company.accounts_overdue
    assert [(o.name, o.min_pct, o.max_pct) for o in owners] == [("Larkfield Holdings Ltd", 75, 100)]
    with pytest.raises(SourceUnavailableError, match="no recorded response"):
        await registry.company("00000000")


@pytest.mark.anyio
async def test_uk_financials_leave_unreported_figures_missing(
    registry: CompaniesHouseClient,
) -> None:
    """Calder & Finch files a balance sheet only: no turnover is reported, so none is shown."""
    years = await CompaniesHouseFinancials(registry).financial_years("99000105")

    latest = years[0]
    assert [year.period_end for year in years] == [date(2024, 9, 30), date(2023, 9, 30)]
    assert latest.revenue is None
    assert latest.net_income is None
    assert latest.equity is not None
    assert latest.equity.value == 144400  # the total, not the dimensional equity breakdown
    assert latest.total_assets is not None
    assert latest.total_assets.source_element == "uk-core:FixedAssets + uk-core:CurrentAssets"


def test_every_demo_company_has_its_expected_outcome() -> None:
    world = yaml.safe_load((SYNTHETIC / "companies.yaml").read_text(encoding="utf-8"))
    truth = json.loads((SYNTHETIC / "ground_truth.json").read_text(encoding="utf-8"))

    customers = [c["name"] for c in world["uk_companies"] if c.get("customer")]
    customers += [c["name"] for c in world["india_companies"]]
    assert sorted(c["name"] for c in truth["companies"]) == sorted(customers)
    assert all(c["scenario"] and c["expected"] for c in truth["companies"])
    assert all(document["fields"] for document in truth["documents"])


@pytest.mark.anyio
async def test_a_shared_director_shows_in_the_appointments(registry: CompaniesHouseClient) -> None:
    """Ardent Freight's director also runs Tarnov Shipping, the sanctioned company."""
    officers = await registry.officers("99000103")
    director = next(officer for officer in officers if "VONDREL" in officer.name.upper())

    assert director.officer_id is not None
    appointments = await registry.officer_appointments(director.officer_id)
    assert {a.company_number for a in appointments} == {"99000103", "99000203"}
    assert await registry.outstanding_charges("99000103") == 0
    assert await registry.insolvency_cases("99000103") == []  # Companies House answers 404
