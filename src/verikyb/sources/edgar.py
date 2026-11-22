"""SEC EDGAR APIs on data.sec.gov: company submissions and XBRL facts for US-listed parents."""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import ClassVar

from verikyb.models import DomainModel
from verikyb.settings import Settings, SourceMode
from verikyb.sources.http import SourceClient, required_in_live_mode


class EdgarCompany(DomainModel):
    cik: str
    name: str
    tickers: list[str] = []
    exchanges: list[str] = []
    sic: str | None = None
    sic_description: str | None = None
    state_of_incorporation: str | None = None


class ConceptFact(DomainModel):
    start: date | None = None  # None for a balance-sheet (instant) value
    end: date
    value: Decimal
    form: str  # e.g. 10-K
    fiscal_period: str | None = None  # FY for an annual report, Q1-Q3 for quarterlies
    filed: date
    accession: str  # the filing the value comes from


class EdgarClient(SourceClient):
    name = "edgar"
    base_url = "https://data.sec.gov"
    rate_limit: ClassVar[tuple[int, float]] = (10, 1.0)  # SEC fair access

    def __init__(self, settings: Settings) -> None:
        user_agent = settings.sec_user_agent
        required_in_live_mode(settings, "VERIKYB_SEC_USER_AGENT", user_agent)
        if settings.sources is SourceMode.LIVE and "@" not in (user_agent or ""):
            raise ValueError('VERIKYB_SEC_USER_AGENT must name you and an email: "Name you@x.com"')
        super().__init__(settings, headers={"User-Agent": user_agent} if user_agent else None)

    async def company(self, cik: str) -> EdgarCompany | None:
        data = await self._get_json(f"/submissions/CIK{cik.zfill(10)}.json")
        if not data:
            return None
        return EdgarCompany(
            cik=cik.zfill(10),
            name=data["name"],
            tickers=data.get("tickers", []),
            exchanges=[exchange for exchange in data.get("exchanges", []) if exchange],
            sic=data.get("sic") or None,
            sic_description=data.get("sicDescription") or None,
            state_of_incorporation=data.get("stateOfIncorporation") or None,
        )

    async def facts(self, cik: str, concepts: Iterable[str], unit: str = "USD") -> dict[str, list[ConceptFact]]:
        """Every reported value of each us-gaap concept; empty for a concept never used."""
        data = await self._get_json(f"/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json")
        gaap = (data or {}).get("facts", {}).get("us-gaap", {})
        return {
            concept: [
                ConceptFact(
                    start=fact.get("start"),
                    end=fact["end"],
                    value=Decimal(str(fact["val"])),  # via str: 0.1 stays 0.1, not 0.1000000001
                    form=fact["form"],
                    fiscal_period=fact.get("fp"),
                    filed=fact["filed"],
                    accession=fact["accn"],
                )
                for fact in gaap.get(concept, {}).get("units", {}).get(unit, [])
            ]
            for concept in concepts
        }
