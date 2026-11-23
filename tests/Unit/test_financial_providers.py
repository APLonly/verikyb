from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from verikyb.finance.provider import (
    CompaniesHouseFinancials,
    EdgarFinancials,
    FinancialDataProvider,
)
from verikyb.settings import Settings, SourceMode
from verikyb.sources.companies_house import CompaniesHouseClient
from verikyb.sources.edgar import EdgarClient
from verikyb.sources.http import Recording, RecordingStore

CH = "https://api.company-information.service.gov.uk"
DOCS = "https://document-api.company-information.service.gov.uk"
SEC = "https://data.sec.gov"
NUMBER = "01234567"
CIK = "0001633917"


def record(fixtures: Path, source: str, url: str, body: Any, content_type: str) -> None:
    RecordingStore(fixtures / source).save(
        Recording(
            url=url,
            status=200,
            content_type=content_type,
            recorded_at=datetime.now(UTC),
            body=body,
        )
    )


Fact = tuple[str, str, str, str]  # element, context (year end, or "flow:<year end>"), value, unit


def ixbrl(facts: list[Fact]) -> str:
    """An iXBRL document with a context per period used: an instant at each year end, and a
    twelve-month duration ending there for "flow:" contexts."""
    contexts = []
    for context in sorted({fact[1] for fact in facts}):
        end = context.removeprefix("flow:")
        period = f"<xbrli:startDate>{int(end[:4]) - 1}{end[4:]}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>" if context.startswith("flow:") else f"<xbrli:instant>{end}</xbrli:instant>"
        contexts.append(f'<xbrli:context id="{context}"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>')
    units = "".join(f'<xbrli:unit id="{unit}"><xbrli:measure>iso4217:{unit}</xbrli:measure></xbrli:unit>' for unit in sorted({fact[3] for fact in facts}))
    tags = "".join(f'<ix:nonFraction name="core:{element}" contextRef="{context}" unitRef="{unit}">{value}</ix:nonFraction>' for element, context, value, unit in facts)
    return (
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"'
        ' xmlns:xbrli="http://www.xbrl.org/2003/instance"'
        ' xmlns:core="http://xbrl.frc.org.uk/fr/2023-01-01/core"><body><ix:header><ix:resources>'
        f"{''.join(contexts)}{units}</ix:resources></ix:header>{tags}</body></html>"
    )


def file_accounts(fixtures: Path, filings: list[tuple[str, list[Fact] | None]]) -> None:
    """Record a filing history, newest first; None stands for a PDF-only filing."""
    items = []
    for transaction, facts in filings:
        document = f"{DOCS}/document/{transaction}"
        items.append(
            {
                "transaction_id": transaction,
                "date": "2025-09-30",
                "category": "accounts",
                "description": "accounts-with-accounts-type-full",
                "links": {"document_metadata": document},
            }
        )
        kind = "application/pdf" if facts is None else "application/xhtml+xml"
        record(fixtures, "companies_house", document, {"resources": {kind: {}}}, "application/json")
        if facts is not None:
            record(
                fixtures,
                "companies_house",
                f"{document}/content",
                ixbrl(facts),
                "application/xhtml+xml",
            )
    record(
        fixtures,
        "companies_house",
        f"{CH}/company/{NUMBER}/filing-history?category=accounts&items_per_page=25",
        {"items": items},
        "application/json",
    )


@pytest.fixture
def replay(tmp_path: Path) -> Settings:
    return Settings(sources=SourceMode.REPLAY, fixtures_dir=tmp_path)


def uk(replay: Settings) -> CompaniesHouseFinancials:
    return CompaniesHouseFinancials(CompaniesHouseClient(replay))


FY25, FY24 = "2025-03-31", "2024-03-31"


# Companies House


@pytest.mark.anyio
async def test_uk_figures_are_decimals_with_their_source_and_period(replay: Settings, tmp_path: Path) -> None:
    file_accounts(
        tmp_path,
        [
            (
                "T2025",
                [
                    ("TurnoverRevenue", f"flow:{FY25}", "1,250,000", "GBP"),
                    ("ProfitLoss", f"flow:{FY25}", "80,500.50", "GBP"),
                    ("Equity", FY25, "144,400", "GBP"),
                    ("CashBankOnHand", FY25, "52,000", "GBP"),
                    ("TurnoverRevenue", f"flow:{FY24}", "1,100,000", "GBP"),
                ],
            )
        ],
    )

    latest, prior = await uk(replay).financial_years(NUMBER)

    assert (latest.period_end, prior.period_end) == (date(2025, 3, 31), date(2024, 3, 31))
    assert latest.currency == "GBP"
    assert latest.revenue is not None
    assert latest.revenue.value == Decimal("1250000")
    assert isinstance(latest.revenue.value, Decimal)
    assert latest.revenue.source_element == "uk-core:TurnoverRevenue"
    assert latest.revenue.evidence_id == f"companies_house:{NUMBER}:T2025"
    assert latest.net_income is not None
    assert latest.net_income.value == Decimal("80500.50")  # exact, never a float
    assert prior.revenue is not None
    assert prior.revenue.value == Decimal("1100000")


@pytest.mark.anyio
async def test_a_figure_that_was_not_filed_stays_missing(replay: Settings, tmp_path: Path) -> None:
    # A balance-sheet-only filer: no profit and loss account, so no revenue or profit.
    file_accounts(
        tmp_path,
        [
            (
                "T2025",
                [
                    ("FixedAssets", FY25, "20,000", "GBP"),
                    ("CurrentAssets", FY25, "150,000", "GBP"),
                    ("Equity", FY25, "144,400", "GBP"),
                ],
            )
        ],
    )

    [year] = await uk(replay).financial_years(NUMBER)

    assert year.revenue is None  # missing, not 0
    assert year.net_income is None
    assert year.operating_profit is None
    assert year.cash is None


@pytest.mark.anyio
async def test_totals_are_derived_and_say_how(replay: Settings, tmp_path: Path) -> None:
    file_accounts(
        tmp_path,
        [
            (
                "T2025",
                [
                    ("FixedAssets", FY25, "20,000", "GBP"),
                    ("CurrentAssets", FY25, "150,000", "GBP"),
                    ("Equity", FY25, "144,400", "GBP"),
                ],
            )
        ],
    )

    [year] = await uk(replay).financial_years(NUMBER)

    assert year.total_assets is not None
    assert year.total_assets.value == Decimal("170000")
    assert year.total_assets.source_element == "uk-core:FixedAssets + uk-core:CurrentAssets"
    assert year.total_liabilities is not None
    assert year.total_liabilities.value == Decimal("25600")
    assert year.total_liabilities.source_element.endswith("- uk-core:Equity")


@pytest.mark.anyio
async def test_a_total_with_a_missing_line_stays_missing(replay: Settings, tmp_path: Path) -> None:
    file_accounts(
        tmp_path,
        [("T2025", [("CurrentAssets", FY25, "150,000", "GBP"), ("Equity", FY25, "1", "GBP")])],
    )

    [year] = await uk(replay).financial_years(NUMBER)

    assert year.total_assets is None  # no fixed assets filed: never assumed to be 0
    assert year.total_liabilities is None


@pytest.mark.anyio
async def test_equity_may_be_filed_as_net_assets(replay: Settings, tmp_path: Path) -> None:
    file_accounts(tmp_path, [("T2025", [("NetAssetsLiabilities", FY25, "9,000", "GBP")])])

    [year] = await uk(replay).financial_years(NUMBER)

    assert year.equity is not None
    assert (year.equity.value, year.equity.source_element) == (
        Decimal("9000"),
        "uk-core:NetAssetsLiabilities",
    )


@pytest.mark.anyio
async def test_a_restated_figure_in_a_later_filing_wins(replay: Settings, tmp_path: Path) -> None:
    file_accounts(
        tmp_path,
        [
            (
                "T2025",
                [
                    ("TurnoverRevenue", f"flow:{FY25}", "1,250,000", "GBP"),
                    ("TurnoverRevenue", f"flow:{FY24}", "1,090,000", "GBP"),  # restated
                ],
            ),
            ("T2024", [("TurnoverRevenue", f"flow:{FY24}", "1,100,000", "GBP")]),
        ],
    )

    years = await uk(replay).financial_years(NUMBER)

    prior = next(year for year in years if year.period_end == date(2024, 3, 31))
    assert prior.revenue is not None
    assert prior.revenue.value == Decimal("1090000")
    assert prior.revenue.evidence_id == f"companies_house:{NUMBER}:T2025"


@pytest.mark.anyio
async def test_pdf_only_filings_are_skipped(replay: Settings, tmp_path: Path) -> None:
    file_accounts(
        tmp_path,
        [("TPDF", None), ("T2024", [("TurnoverRevenue", f"flow:{FY24}", "1,100,000", "GBP")])],
    )

    [year] = await uk(replay).financial_years(NUMBER)

    assert year.period_end == date(2024, 3, 31)


@pytest.mark.anyio
async def test_one_currency_per_year_and_non_money_units_ignored(replay: Settings, tmp_path: Path) -> None:
    file_accounts(
        tmp_path,
        [
            (
                "T2025",
                [
                    ("Equity", FY25, "10", "shares"),  # not money: never sets the currency
                    ("TurnoverRevenue", f"flow:{FY25}", "1,000", "EUR"),
                    ("Equity", FY25, "500", "EUR"),
                ],
            ),
            ("T2024", [("CashBankOnHand", FY25, "99", "GBP")]),
        ],
    )

    [year] = await uk(replay).financial_years(NUMBER)

    assert year.currency == "EUR"  # the newest filing's
    assert year.revenue is not None
    assert year.revenue.value == Decimal("1000")
    assert year.cash is None  # filed in GBP: left out, never mixed in as EUR


@pytest.mark.anyio
async def test_at_most_five_years_newest_first(replay: Settings, tmp_path: Path) -> None:
    facts = [("TurnoverRevenue", f"flow:{year}-03-31", str(year), "GBP") for year in range(2018, 2026)]
    file_accounts(tmp_path, [("T2025", facts)])

    years = await uk(replay).financial_years(NUMBER)

    assert [y.period_end.year for y in years] == [2025, 2024, 2023, 2022, 2021]


@pytest.mark.anyio
async def test_a_company_without_accounts_has_no_years(replay: Settings, tmp_path: Path) -> None:
    file_accounts(tmp_path, [])

    assert await uk(replay).financial_years(NUMBER) == []


# EDGAR


def fact(end: str, val: float, filed: str, accn: str, **extra: Any) -> dict[str, Any]:
    start = extra.pop("start", f"{end[:4]}-01-01")  # a calendar year, unless given
    return {
        "end": end,
        "val": val,
        "form": extra.pop("form", "10-K"),
        "fp": extra.pop("fp", "FY"),
        "filed": filed,
        "accn": accn,
        **({"start": start} if start else {}),
        **extra,
    }


def edgar_facts(tmp_path: Path, concepts: dict[str, list[dict[str, Any]]]) -> None:
    gaap = {name: {"units": {"USD": facts}} for name, facts in concepts.items()}
    record(
        tmp_path,
        "edgar",
        f"{SEC}/api/xbrl/companyfacts/CIK{CIK}.json",
        {"facts": {"us-gaap": gaap}},
        "application/json",
    )


def us(replay: Settings) -> EdgarFinancials:
    return EdgarFinancials(EdgarClient(replay))


@pytest.mark.anyio
async def test_us_figures_are_annual_decimals_with_their_source(replay: Settings, tmp_path: Path) -> None:
    edgar_facts(
        tmp_path,
        {
            "Revenues": [
                # The fourth quarter, shown inside the same 10-K, comes first: it must not win.
                fact("2024-12-31", 8000000000, "2025-02-04", "A-2025", start="2024-10-01"),
                fact("2024-12-31", 31797000000, "2025-02-04", "A-2025", start="2024-01-01"),
            ],
            "Assets": [
                fact("2024-12-31", 81613000000.5, "2025-02-04", "A-2025", start=None),
                # A quarter-end balance sheet from a 10-Q: not a year.
                fact(
                    "2024-09-30",
                    80000000000,
                    "2024-10-29",
                    "Q-2024",
                    start=None,
                    form="10-Q",
                    fp="Q3",
                ),
            ],
        },
    )

    [year] = await us(replay).financial_years(CIK)

    assert (year.period_end, year.currency) == (date(2024, 12, 31), "USD")
    assert year.revenue is not None
    assert year.revenue.value == Decimal("31797000000")  # the year, not the quarter in it
    assert (year.revenue.source_element, year.revenue.evidence_id) == (
        "us-gaap:Revenues",
        f"edgar:{CIK}:A-2025",
    )
    assert year.total_assets is not None
    assert year.total_assets.value == Decimal("81613000000.5")
    assert year.net_income is None  # never reported: missing, not 0


@pytest.mark.anyio
async def test_the_latest_filed_value_wins_including_an_amendment(replay: Settings, tmp_path: Path) -> None:
    edgar_facts(
        tmp_path,
        {
            "NetIncomeLoss": [
                fact("2023-12-31", 4246000000, "2024-02-05", "A-2024"),
                fact("2023-12-31", 4240000000, "2025-02-04", "A-2025"),  # a later comparative
                fact("2023-12-31", 4230000000, "2025-06-01", "AMEND", form="10-K/A"),
            ]
        },
    )

    [year] = await us(replay).financial_years(CIK)

    assert year.net_income is not None
    assert (year.net_income.value, year.net_income.evidence_id) == (
        Decimal("4230000000"),
        f"edgar:{CIK}:AMEND",
    )


@pytest.mark.anyio
async def test_revenue_concepts_are_tried_in_order(replay: Settings, tmp_path: Path) -> None:
    edgar_facts(
        tmp_path,
        {
            "Revenues": [fact("2024-12-31", 100, "2025-02-04", "A-2025")],
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                fact("2024-12-31", 99, "2025-02-04", "A-2025"),
                fact("2023-12-31", 90, "2024-02-05", "A-2024"),
            ],
        },
    )

    latest, prior = await us(replay).financial_years(CIK)

    assert latest.revenue is not None
    assert latest.revenue.source_element == "us-gaap:Revenues"  # preferred when both exist
    assert prior.revenue is not None
    assert prior.revenue.source_element == ("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax")


@pytest.mark.anyio
async def test_a_company_without_annual_facts_has_no_years(replay: Settings, tmp_path: Path) -> None:
    edgar_facts(tmp_path, {"Revenues": [fact("2024-09-30", 1, "2024-10-29", "Q", form="10-Q")]})

    assert await us(replay).financial_years(CIK) == []


def test_both_providers_fit_the_interface(replay: Settings) -> None:
    providers: list[FinancialDataProvider] = [uk(replay), us(replay)]
    assert len(providers) == 2
