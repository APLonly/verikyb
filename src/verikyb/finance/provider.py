"""Multi-year financials from free public sources, behind one interface."""

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from typing import ClassVar, Protocol

from verikyb.models import Figure, FinancialYear
from verikyb.sources.ch_accounts import XbrlFact, parse_ixbrl
from verikyb.sources.companies_house import CompaniesHouseClient
from verikyb.sources.edgar import ConceptFact, EdgarClient

YEARS = 5
_CURRENCY = re.compile(r"^[A-Z]{3}$")  # ISO 4217, as FinancialYear requires


class FinancialDataProvider(Protocol):
    async def financial_years(self, entity_id: str) -> list[FinancialYear]: ...


class CompaniesHouseFinancials:
    """UK companies, from iXBRL accounts filed at Companies House. `entity_id`: company number."""

    REPORTED: ClassVar[dict[str, tuple[str, ...]]] = {
        "revenue": ("TurnoverRevenue",),
        "operating_profit": ("OperatingProfitLoss",),
        "net_income": ("ProfitLoss",),
        "equity": ("Equity", "NetAssetsLiabilities"),
        "cash": ("CashBankOnHand",),
    }
    MONEY: ClassVar[set[str]] = {e for elements in REPORTED.values() for e in elements} | {
        "FixedAssets",
        "CurrentAssets",
    }

    def __init__(self, companies_house: CompaniesHouseClient, filings: int = 3) -> None:
        self._companies_house = companies_house
        self._filings = filings

    async def financial_years(self, entity_id: str) -> list[FinancialYear]:
        # Newest filing first: a figure restated in a later filing replaces the one first filed.
        facts: dict[tuple[str, date], tuple[XbrlFact, str]] = {}
        for filing in (await self._companies_house.filings(entity_id))[: self._filings]:
            document = await self._companies_house.accounts_document(filing)
            if document is None:
                continue  # paper or PDF-only filing: nothing machine-readable
            evidence_id = f"companies_house:{entity_id}:{filing.transaction_id}"
            for fact in parse_ixbrl(document):
                if fact.element in self.MONEY and _CURRENCY.match(fact.unit):
                    facts.setdefault((fact.element, fact.period_end), (fact, evidence_id))

        by_period: dict[date, dict[str, tuple[XbrlFact, str]]] = defaultdict(dict)
        for (element, period_end), found in facts.items():  # newest filing's facts come first
            by_period[period_end][element] = found
        latest = sorted(by_period, reverse=True)[:YEARS]
        return [self._year(period_end, by_period[period_end]) for period_end in latest]

    def _year(self, period_end: date, found: dict[str, tuple[XbrlFact, str]]) -> FinancialYear:
        # One currency per year: the newest filing's. A figure in another currency is left out rather than mixed in under the wrong label.
        currency = next(iter(found.values()))[0].unit
        found = {element: hit for element, hit in found.items() if hit[0].unit == currency}

        def figure(*elements: str) -> Figure | None:
            for element in elements:
                if element in found:
                    fact, evidence_id = found[element]
                    return Figure(
                        value=fact.value,
                        source_element=f"uk-core:{element}",
                        evidence_id=evidence_id,
                    )
            return None

        figures = {name: figure(*elements) for name, elements in self.REPORTED.items()}
        fixed, current, equity = figure("FixedAssets"), figure("CurrentAssets"), figures["equity"]
        if fixed and current:
            figures["total_assets"] = Figure(
                value=fixed.value + current.value,
                source_element="uk-core:FixedAssets + uk-core:CurrentAssets",
                evidence_id=current.evidence_id,
            )
            if equity:
                figures["total_liabilities"] = Figure(
                    value=fixed.value + current.value - equity.value,
                    source_element=(f"uk-core:FixedAssets + uk-core:CurrentAssets - {equity.source_element}"),
                    evidence_id=equity.evidence_id,
                )
        return FinancialYear.model_validate({"period_end": period_end, "currency": currency, **figures})


class EdgarFinancials:
    """US-listed parents, from XBRL facts in annual reports on EDGAR. `entity_id`: the CIK.

    Concepts are tried in order: companies report revenue under different us-gaap names.
    """

    CONCEPTS: ClassVar[dict[str, tuple[str, ...]]] = {
        "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        "operating_profit": ("OperatingIncomeLoss",),
        "net_income": ("NetIncomeLoss",),
        "total_assets": ("Assets",),
        "total_liabilities": ("Liabilities",),
        "equity": ("StockholdersEquity",),
        "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    }

    def __init__(self, edgar: EdgarClient) -> None:
        self._edgar = edgar

    async def financial_years(self, entity_id: str) -> list[FinancialYear]:
        reported = await self._edgar.facts(entity_id, _flatten(self.CONCEPTS.values()))
        figures: dict[date, dict[str, Figure]] = defaultdict(dict)
        for name, concepts in self.CONCEPTS.items():
            for concept in concepts:
                # A year's value is repeated as a comparative in later annual reports; the
                # latest filed wins, because it carries any restatement (including a 10-K/A).
                latest: dict[date, ConceptFact] = {}
                for fact in filter(_annual, reported[concept]):
                    if fact.end not in latest or fact.filed > latest[fact.end].filed:
                        latest[fact.end] = fact
                for end, fact in latest.items():
                    figures[end].setdefault(
                        name,
                        Figure(
                            value=fact.value,
                            source_element=f"us-gaap:{concept}",
                            evidence_id=f"edgar:{entity_id}:{fact.accession}",
                        ),
                    )
        latest_years = sorted(figures, reverse=True)[:YEARS]
        return [FinancialYear.model_validate({"period_end": end, "currency": "USD", **figures[end]}) for end in latest_years]


ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})


def _annual(fact: ConceptFact) -> bool:
    """A full-year value from an annual report: not a quarter shown inside a 10-K."""
    if fact.form not in ANNUAL_FORMS or fact.fiscal_period != "FY":
        return False
    return fact.start is None or 350 <= (fact.end - fact.start).days <= 380


def _flatten(groups: Iterable[tuple[str, ...]]) -> list[str]:
    return [item for group in groups for item in group]
