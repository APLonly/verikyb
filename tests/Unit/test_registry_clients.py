import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from verikyb.models import PartyKind
from verikyb.settings import Settings, SourceMode
from verikyb.sources.ch_accounts import parse_ixbrl
from verikyb.sources.companies_house import CompaniesHouseClient, Filing
from verikyb.sources.http import Recording, RecordingStore, SourceUnavailableError
from verikyb.sources.mock_india import MockIndiaRegistry
from verikyb.sources.registry import RegistryAdapter

CH = "https://api.company-information.service.gov.uk"
DOCS = "https://document-api.company-information.service.gov.uk"


def record(fixtures: Path, url: str, body: Any, status: int = 200, kind: str = "json") -> None:
    content_type = "application/json" if kind == "json" else "application/xhtml+xml"
    RecordingStore(fixtures / "companies_house").save(
        Recording(
            url=url,
            status=status,
            content_type=content_type,
            recorded_at=datetime.now(UTC),
            body=body,
        )
    )


@pytest.fixture
def fixtures(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def ch(fixtures: Path) -> CompaniesHouseClient:
    return CompaniesHouseClient(Settings(sources=SourceMode.REPLAY, fixtures_dir=fixtures))


PROFILE = {
    "company_number": "01234567",
    "company_name": "EXAMPLE PAYMENTS LIMITED",
    "company_status": "active",
    "date_of_creation": "2010-04-01",
    "type": "ltd",
    "registered_office_address": {
        "premises": "6th Floor",
        "address_line_1": "1 Example Street",
        "locality": "London",
        "postal_code": "EC1A 1AA",
    },
    "sic_codes": ["64999", "66190"],
    "previous_company_names": [{"name": "EXAMPLE TRANSFERS LIMITED"}],
    "accounts": {
        "last_accounts": {"made_up_to": "2025-03-31"},
        "next_due": "2026-12-31",
        "overdue": False,
    },
    "confirmation_statement": {"overdue": True},
    "has_charges": True,
}


# Companies House


@pytest.mark.anyio
async def test_company_profile_maps_every_field(fixtures: Path, ch: CompaniesHouseClient) -> None:
    record(fixtures, f"{CH}/company/01234567", PROFILE)

    company = await ch.company("01234567")

    assert company is not None
    assert (company.number, company.name, company.jurisdiction) == (
        "01234567",
        "EXAMPLE PAYMENTS LIMITED",
        "GB",
    )
    assert (company.status, company.legal_form) == ("active", "ltd")
    assert company.incorporated_on == date(2010, 4, 1)
    assert company.address is not None
    assert company.address.lines == ["6th Floor 1 Example Street"]
    assert (company.address.locality, company.address.postal_code) == ("London", "EC1A 1AA")
    assert company.activity_codes == ["64999", "66190"]
    assert company.previous_names == ["EXAMPLE TRANSFERS LIMITED"]
    assert company.last_accounts_made_up_to == date(2025, 3, 31)
    assert company.accounts_next_due == date(2026, 12, 31)
    assert company.accounts_overdue is False
    assert company.confirmation_statement_overdue is True
    assert company.has_charges is True
    assert company.has_insolvency_history is False


@pytest.mark.anyio
async def test_an_unknown_company_is_none(fixtures: Path, ch: CompaniesHouseClient) -> None:
    record(fixtures, f"{CH}/company/00000000", {"errors": []}, status=404)

    assert await ch.company("00000000") is None
    await ch.aclose()


@pytest.mark.anyio
async def test_search_reads_the_differently_named_fields(fixtures: Path, ch: CompaniesHouseClient) -> None:
    hit = {
        "company_number": "01234567",
        "title": "EXAMPLE PAYMENTS LIMITED",
        "company_status": "active",
        "company_type": "ltd",
        "date_of_creation": "2010-04-01",
        "address": {"premises": "6th Floor", "address_line_1": "1 Example Street"},
    }
    record(fixtures, f"{CH}/search/companies?q=example+payments&items_per_page=20", {"items": [hit]})

    [company] = await ch.search("example payments")

    assert (company.name, company.legal_form) == ("EXAMPLE PAYMENTS LIMITED", "ltd")
    assert company.address is not None
    assert company.address.lines == ["6th Floor 1 Example Street"]


def officers_page(start: int, count: int, total: int) -> dict[str, Any]:
    items = [
        {
            "name": f"OFFICER, Number {n}",
            "officer_role": "director",
            "appointed_on": "2020-01-01",
            "links": {"officer": {"appointments": f"/officers/id{n}/appointments"}},
        }
        for n in range(start, start + count)
    ]
    return {"items": items, "total_results": total, "start_index": start}


@pytest.mark.anyio
async def test_officers_follow_every_page(fixtures: Path, ch: CompaniesHouseClient) -> None:
    url = f"{CH}/company/01234567/officers?items_per_page=100"
    record(fixtures, f"{url}&start_index=0", officers_page(0, 100, 130))
    record(fixtures, f"{url}&start_index=100", officers_page(100, 30, 130))

    officers = await ch.officers("01234567")

    assert len(officers) == 130
    assert officers[129].officer_id == "id129"


@pytest.mark.anyio
async def test_an_officer_keeps_its_id_and_partial_birth_date(fixtures: Path, ch: CompaniesHouseClient) -> None:
    officer = {
        "name": "SMITH, Jane Ann",
        "officer_role": "director",
        "appointed_on": "2019-06-01",
        "resigned_on": "2024-02-29",
        "nationality": "British",
        "country_of_residence": "England",
        "date_of_birth": {"year": 1975, "month": 9},
        "links": {"officer": {"appointments": "/officers/AbC123xyz/appointments"}},
    }
    record(
        fixtures,
        f"{CH}/company/01234567/officers?items_per_page=100&start_index=0",
        {"items": [officer], "total_results": 1},
    )

    [jane] = await ch.officers("01234567")

    assert (jane.name, jane.role, jane.officer_id) == ("SMITH, Jane Ann", "director", "AbC123xyz")
    assert (jane.appointed_on, jane.resigned_on) == (date(2019, 6, 1), date(2024, 2, 29))
    assert (jane.birth_year, jane.birth_month) == (1975, 9)


@pytest.mark.anyio
async def test_an_unknown_company_has_no_officers(fixtures: Path, ch: CompaniesHouseClient) -> None:
    record(
        fixtures,
        f"{CH}/company/00000000/officers?items_per_page=100&start_index=0",
        {"errors": []},
        status=404,
    )

    assert await ch.officers("00000000") == []


@pytest.mark.anyio
async def test_owners_map_persons_entities_and_share_bands(fixtures: Path, ch: CompaniesHouseClient) -> None:
    items = [
        {
            "kind": "corporate-entity-person-with-significant-control",
            "name": "EXAMPLE HOLDINGS PLC",
            "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
            "identification": {"registration_number": "07654321", "country_registered": "England"},
            "notified_on": "2016-04-06",
        },
        {
            "kind": "individual-person-with-significant-control",
            "name": "Mr John Example",
            "natures_of_control": [
                "voting-rights-25-to-50-percent",
                "ownership-of-shares-more-than-25-percent-registered-overseas-entity",
            ],
            "country_of_residence": "United Kingdom",
            "ceased_on": "2023-01-31",
        },
        {"kind": "super-secure-person-with-significant-control"},
        {
            "kind": "individual-person-with-significant-control",
            "name": "Ms Voting Only",
            "natures_of_control": ["voting-rights-25-to-50-percent"],
        },
        {"kind": "persons-with-significant-control-statement", "statement": "no-registrable"},
    ]
    record(
        fixtures,
        f"{CH}/company/01234567/persons-with-significant-control?items_per_page=100&start_index=0",
        {"items": items, "total_results": len(items)},
    )

    holding, john, secure, voting = await ch.owners("01234567")  # the statement is left out

    assert (holding.kind, holding.min_pct, holding.max_pct) == (PartyKind.ENTITY, 75, 100)
    assert (holding.registration_number, holding.country) == ("07654321", "England")
    assert holding.notified_on == date(2016, 4, 6)
    assert (john.kind, john.min_pct, john.max_pct) == (PartyKind.PERSON, 25, 100)
    assert john.ceased_on == date(2023, 1, 31)
    assert (secure.name, secure.kind) == ("Protected person", PartyKind.PERSON)
    assert (voting.min_pct, voting.max_pct) == (None, None)  # control without shares
    assert voting.controls == ["voting-rights-25-to-50-percent"]


@pytest.mark.anyio
async def test_accounts_document_fetches_the_ixbrl_behind_a_filing(fixtures: Path, ch: CompaniesHouseClient) -> None:
    record(
        fixtures,
        f"{CH}/company/01234567/filing-history?category=accounts&items_per_page=25",
        {
            "items": [
                {
                    "transaction_id": "MzA1",
                    "date": "2025-09-30",
                    "category": "accounts",
                    "description": "accounts-with-accounts-type-full",
                    "description_values": {"made_up_date": "2025-03-31"},
                    "links": {"document_metadata": f"{DOCS}/document/abc"},
                }
            ]
        },
    )
    record(fixtures, f"{DOCS}/document/abc", {"resources": {"application/xhtml+xml": {}}})
    record(fixtures, f"{DOCS}/document/abc/content", "<html>accounts</html>", kind="xhtml")

    [filing] = await ch.filings("01234567")
    document = await ch.accounts_document(filing)

    assert (filing.made_up_to, filing.filed_on) == (date(2025, 3, 31), date(2025, 9, 30))
    assert document == "<html>accounts</html>"


@pytest.mark.anyio
async def test_a_paper_or_pdf_filing_has_no_accounts_document(fixtures: Path, ch: CompaniesHouseClient) -> None:
    record(fixtures, f"{DOCS}/document/pdf", {"resources": {"application/pdf": {}}})
    pdf = Filing(
        transaction_id="t",
        filed_on=date(2025, 1, 1),
        category="accounts",
        description="",
        document_url=f"{DOCS}/document/pdf",
    )

    assert await ch.accounts_document(pdf) is None
    assert await ch.accounts_document(pdf.model_copy(update={"document_url": None})) is None


def test_live_mode_needs_a_companies_house_key(fixtures: Path) -> None:
    with pytest.raises(ValueError, match="VERIKYB_COMPANIES_HOUSE_API_KEY"):
        CompaniesHouseClient(Settings(sources=SourceMode.LIVE, fixtures_dir=fixtures))


@pytest.mark.anyio
async def test_the_key_is_sent_as_basic_auth_with_an_empty_password(fixtures: Path) -> None:
    settings = Settings(sources=SourceMode.LIVE, fixtures_dir=fixtures, companies_house_api_key="my-key")
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=PROFILE)

    ch = CompaniesHouseClient(settings)
    ch._http._transport._network = httpx.MockTransport(answer)  # type: ignore[attr-defined]
    await ch.company("01234567")
    await ch.aclose()

    assert sent[0].headers["authorization"] == httpx.BasicAuth("my-key", "")._auth_header


# The iXBRL accounts parser

IXBRL = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns:core="http://xbrl.frc.org.uk/fr/2023-01-01/core"
      xmlns:bus="http://xbrl.frc.org.uk/cd/2023-01-01/business">
<body>
<ix:header><ix:resources>
  <xbrli:context id="year"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
    </xbrli:entity><xbrli:period><xbrli:startDate>2024-04-01</xbrli:startDate>
    <xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="end"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
    </xbrli:entity><xbrli:period><xbrli:instant>2025-03-31</xbrli:instant></xbrli:period>
  </xbrli:context>
  <xbrli:context id="end-by-component"><xbrli:entity>
    <xbrli:identifier scheme="x">1</xbrli:identifier>
    <xbrli:segment><xbrldi:explicitMember dimension="core:EquityClassesDimension"
      >core:ShareCapital</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
    <xbrli:period><xbrli:instant>2025-03-31</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:unit id="GBP"><xbrli:measure>iso4217:GBP</xbrli:measure></xbrli:unit>
</ix:resources></ix:header>
<ix:nonFraction name="core:TurnoverRevenue" contextRef="year" unitRef="GBP" scale="3"
  format="ixt:num-dot-decimal">1,250</ix:nonFraction>
<ix:nonFraction name="core:ProfitLoss" contextRef="year" unitRef="GBP" sign="-"
  format="ixt:num-dot-decimal">(4,321)</ix:nonFraction>
<ix:nonFraction name="core:Equity" contextRef="end-by-component" unitRef="GBP">100</ix:nonFraction>
<ix:nonFraction name="core:Equity" contextRef="end" unitRef="GBP">144,400</ix:nonFraction>
<ix:nonFraction name="core:Equity" contextRef="end" unitRef="GBP">144,400</ix:nonFraction>
<ix:nonFraction name="core:Creditors" contextRef="end" unitRef="GBP"
  format="ixt:fixed-zero">-</ix:nonFraction>
<ix:nonFraction name="core:CashBankOnHand" contextRef="end" unitRef="GBP"
  format="ixt:numcommadecimal">1.234,50</ix:nonFraction>
<ix:nonFraction name="core:Debtors" contextRef="end" unitRef="GBP" xsi:nil="true"></ix:nonFraction>
<ix:nonFraction name="core:Provisions" contextRef="end" unitRef="GBP">1.2.3</ix:nonFraction>
<ix:nonFraction name="bus:AverageNumberEmployees" contextRef="year">12</ix:nonFraction>
</body></html>"""


def test_ixbrl_keeps_headline_core_figures_only() -> None:
    facts = {fact.element: fact for fact in parse_ixbrl(IXBRL)}

    assert set(facts) == {"TurnoverRevenue", "ProfitLoss", "Equity", "Creditors", "CashBankOnHand"}
    assert facts["TurnoverRevenue"].value == Decimal(1_250_000)  # scale 3: thousands
    assert facts["TurnoverRevenue"].period_start == date(2024, 4, 1)
    assert facts["ProfitLoss"].value == Decimal(-4321)
    assert facts["Equity"].value == Decimal(144_400)  # the total, not the dimensional 100
    assert facts["Equity"].period_start is None  # a balance-sheet instant
    assert facts["Equity"].unit == "GBP"
    assert facts["Creditors"].value == 0  # a dash meaning zero
    assert facts["CashBankOnHand"].value == Decimal("1234.50")  # comma as the decimal mark


def test_ixbrl_keeps_a_fact_tagged_twice_once() -> None:
    assert [fact.element for fact in parse_ixbrl(IXBRL)].count("Equity") == 1


# The mock India registry

INDIA = [
    {
        "company": {
            "number": "U72900KA2015PTC082143",
            "name": "Example Software Private Limited",
            "jurisdiction": "IN",
            "status": "Active",
        },
        "officers": [{"name": "Asha Rao", "role": "Director", "officer_id": "07654321"}],
        "owners": [{"name": "Asha Rao", "kind": "person", "min_pct": 60, "max_pct": 60}],
    }
]


def india(fixtures: Path) -> MockIndiaRegistry:
    path = fixtures / "india_registry" / "companies.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(INDIA), encoding="utf-8")
    return MockIndiaRegistry(Settings(fixtures_dir=fixtures))


@pytest.mark.anyio
async def test_india_answers_by_cin_and_by_name(fixtures: Path) -> None:
    registry = india(fixtures)
    cin = "U72900KA2015PTC082143"

    company = await registry.company(cin)
    assert company is not None
    assert company.jurisdiction == "IN"
    assert [c.number for c in await registry.search("EXAMPLE software")] == [cin]
    assert [o.officer_id for o in await registry.officers(cin)] == ["07654321"]
    assert [o.min_pct for o in await registry.owners(cin)] == [60]


@pytest.mark.anyio
async def test_india_finds_nothing_for_unknown_companies_or_an_empty_name(fixtures: Path) -> None:
    registry = india(fixtures)

    assert await registry.company("U00000XX0000PTC000000") is None
    assert await registry.officers("U00000XX0000PTC000000") == []
    assert await registry.owners("U00000XX0000PTC000000") == []
    assert await registry.search("   ") == []


def test_india_without_records_is_unavailable(fixtures: Path) -> None:
    with pytest.raises(SourceUnavailableError, match="make data"):
        MockIndiaRegistry(Settings(fixtures_dir=fixtures))


def test_both_registries_fit_the_interface(fixtures: Path, ch: CompaniesHouseClient) -> None:
    registries: list[RegistryAdapter] = [ch, india(fixtures)]

    assert [r.jurisdiction for r in registries] == ["GB", "IN"]
