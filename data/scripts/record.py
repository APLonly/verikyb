"""Record the demo firms Companies House data into data/fixtures, for the tests to replay."""

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

from verikyb.config import RealFirm, load_demo_set
from verikyb.finance.provider import CompaniesHouseFinancials, EdgarFinancials
from verikyb.settings import Settings, SourceMode
from verikyb.sources.companies_house import CompaniesHouseClient
from verikyb.sources.edgar import EdgarClient
from verikyb.sources.fca import FcaClient, FirmResource
from verikyb.sources.gleif import GleifClient
from verikyb.sources.http import SourceClient, SourceUnavailableError

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def connect[T: SourceClient](build: Callable[[Settings], T], settings: Settings) -> T | None:
    """The client, or None without its credentials (the constructor names what is missing)."""
    try:
        return build(settings)
    except ValueError as exc:
        print(f"skipped  {exc}")
        return None


async def record() -> int:
    settings = Settings(sources=SourceMode.LIVE, cache_ttl_hours=0)  # 0: always call the API
    gleif = GleifClient(settings)
    edgar = connect(EdgarClient, settings)
    registry = connect(CompaniesHouseClient, settings)
    fca = connect(FcaClient, settings)
    failed = 0
    try:
        for firm in load_demo_set(CONFIG_DIR).real_firms:
            try:
                await _gleif(gleif, firm)
                if edgar and firm.us_parent_cik:
                    await edgar.company(firm.us_parent_cik)
                    await EdgarFinancials(edgar).financial_years(firm.us_parent_cik)
                if registry:
                    await _companies_house(registry, firm)
                if fca:
                    await _fca(fca, firm)
            except SourceUnavailableError as exc:
                print(f"FAILED   {firm.name}: {exc}", file=sys.stderr)
                failed += 1
                continue
            print(f"ok       {firm.name}")
    finally:
        for client in (gleif, edgar, registry, fca):
            if client:
                await client.aclose()
    print(f"recorded into {settings.fixtures_dir}")
    return 1 if failed else 0


async def _gleif(gleif: GleifClient, firm: RealFirm) -> None:
    for record in await gleif.by_registration_number(firm.company_number):
        await gleif.record(record.lei)
        await gleif.direct_parent(record.lei)
        await gleif.ultimate_parent(record.lei)


async def _companies_house(registry: CompaniesHouseClient, firm: RealFirm) -> None:
    number = firm.company_number
    await registry.search(firm.name)
    await registry.company(number)
    await registry.owners(number)
    await registry.outstanding_charges(number)
    await registry.insolvency_cases(number)
    for officer in await registry.officers(number):
        if officer.officer_id and officer.resigned_on is None:
            await registry.officer_appointments(officer.officer_id)
    await CompaniesHouseFinancials(registry).financial_years(number)


async def _fca(fca: FcaClient, firm: RealFirm) -> None:
    """Only a firm the search finds exactly once is recorded; anything else needs a person."""
    matches = [hit for hit in await fca.search_firms(firm.name) if hit.name.casefold() == firm.name.casefold()]
    if len(matches) != 1:
        print(f"         FCA: {len(matches)} exact matches for {firm.name}; resolve it by hand")
        return
    frn = matches[0].frn
    await fca.firm(frn)
    await fca.permissions(frn)
    for resource in FirmResource:
        await fca.firm_rows(frn, resource)


if __name__ == "__main__":
    sys.exit(asyncio.run(record()))
