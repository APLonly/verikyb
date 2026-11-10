"""Record the demo firms Companies House data into data/fixtures, for the tests to replay."""

import asyncio
import sys
from pathlib import Path

from verikyb.config import load_demo_set
from verikyb.settings import Settings, SourceMode
from verikyb.sources.companies_house import CompaniesHouseClient
from verikyb.sources.http import SourceUnavailableError

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


async def record() -> int:
    settings = Settings(sources=SourceMode.LIVE, cache_ttl_hours=0)  # 0: always call the API
    try:
        registry = CompaniesHouseClient(settings)
    except ValueError as exc:  # no key
        print(f"cannot record: {exc}", file=sys.stderr)
        return 1
    failed = 0
    try:
        for firm in load_demo_set(CONFIG_DIR).real_firms:
            try:
                await registry.search(firm.name)
                company = await registry.company(firm.company_number)
                officers = await registry.officers(firm.company_number)
                owners = await registry.owners(firm.company_number)
            except SourceUnavailableError as exc:
                print(f"FAILED  {firm.name}: {exc}", file=sys.stderr)
                failed += 1
                continue
            found = company.name if company else "NOT FOUND"
            print(f"ok      {firm.name}: {found}, {len(officers)} officers, {len(owners)} owners")
    finally:
        await registry.aclose()
    print(f"recorded into {settings.fixtures_dir / CompaniesHouseClient.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(record()))
