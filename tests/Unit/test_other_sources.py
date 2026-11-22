from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from verikyb.settings import Settings, SourceMode
from verikyb.sources.edgar import EdgarClient
from verikyb.sources.fca import FcaClient, FirmResource
from verikyb.sources.gleif import GleifClient
from verikyb.sources.http import Recording, RecordingStore, SourceClient, SourceUnavailableError
from verikyb.sources.news import GdeltNews, NewsSearch

FCA = "https://register.fca.org.uk/services/V0.1"
GLEIF = "https://api.gleif.org/api/v1"
SEC = "https://data.sec.gov"
GDELT = "https://api.gdeltproject.org/api/v2"


def record(
    fixtures: Path,
    source: str,
    url: str,
    body: Any,
    status: int = 200,
    content_type: str = "application/json",
) -> None:
    RecordingStore(fixtures / source).save(
        Recording(
            url=url,
            status=status,
            content_type=content_type,
            recorded_at=datetime.now(UTC),
            body=body,
        )
    )


@pytest.fixture
def replay(tmp_path: Path) -> Settings:
    return Settings(sources=SourceMode.REPLAY, fixtures_dir=tmp_path)


def live_answering(client: SourceClient, status: int, body: Any = None, text: str | None = None) -> list[httpx.Request]:
    """Point a live client at a fake network; returns the requests it sends."""
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status, text=text) if text is not None else httpx.Response(status, json=body)

    client._http._transport._network = httpx.MockTransport(answer)  # type: ignore[attr-defined]
    return sent


def wrapped(data: Any, status: str = "FSR-API-04-01-00", message: str = "Ok") -> dict[str, Any]:
    """The FCA's envelope around every answer."""
    return {"Status": status, "ResultInfo": None, "Message": message, "Data": data}


# FCA


@pytest.mark.anyio
async def test_fca_search_returns_firms_without_the_postcode_suffix(replay: Settings, tmp_path: Path) -> None:
    rows = [
        {
            "Reference Number": "900507",
            "Name": "Wise Payments Limited (Postcode: EC2A 2FA)",
            "Status": "Authorised",
            "Type of business or Individual": "Firm",
        },
        {
            "Reference Number": "ABC123",
            "Name": "Someone Wise",
            "Status": "Approved",
            "Type of business or Individual": "Individual",
        },
    ]
    record(
        tmp_path,
        "fca",
        f"{FCA}/Search?q=wise&type=firm",
        wrapped(rows, message="Ok. Search successful"),
    )

    [hit] = await FcaClient(replay).search_firms("wise")

    assert (hit.frn, hit.name, hit.status) == ("900507", "Wise Payments Limited", "Authorised")


@pytest.mark.anyio
async def test_fca_no_search_result_is_an_answer(replay: Settings, tmp_path: Path) -> None:
    record(
        tmp_path,
        "fca",
        f"{FCA}/Search?q=nobody&type=firm",
        wrapped(None, "FSR-API-04-01-11", "No search result found"),
    )

    assert await FcaClient(replay).search_firms("nobody") == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("FSR-API-01-01-11", "Unauthorised: invalid key"),
        ("FSR-API-99-99-99", ""),
        ("FSR-API-05-01-00", "Rate limit exceeded"),
    ],
)
async def test_fca_errors_are_unavailable_never_not_on_the_register(replay: Settings, tmp_path: Path, status: str, message: str) -> None:
    record(tmp_path, "fca", f"{FCA}/Search?q=wise&type=firm", wrapped(None, status, message))

    with pytest.raises(SourceUnavailableError, match=status):
        await FcaClient(replay).search_firms("wise")


@pytest.mark.anyio
async def test_fca_firm_details_and_permissions(replay: Settings, tmp_path: Path) -> None:
    firm = {
        "FRN": 900507,
        "Organisation Name": "Wise Payments Limited",
        "Status": "Authorised",
        "Status Effective Date": "02/03/2011",
        "Business Type": "Regulated",
        "Companies House Number": "07209813",
    }
    record(tmp_path, "fca", f"{FCA}/Firm/900507", wrapped([firm], "FSR-API-02-01-00", "Ok. Firm Found"))
    permissions = [{"Issuing electronic money": [{"Limitation": []}]}, {"Payment services": []}]
    record(tmp_path, "fca", f"{FCA}/Firm/900507/Permissions", wrapped(permissions))
    record(tmp_path, "fca", f"{FCA}/Firm/900507/Names", wrapped({"Previous Name": "TransferWise"}))
    fca = FcaClient(replay)

    details = await fca.firm("900507")

    assert details is not None
    assert (details.frn, details.status, details.companies_house_number) == (
        "900507",
        "Authorised",
        "07209813",
    )
    assert await fca.permissions("900507") == ["Issuing electronic money", "Payment services"]
    assert await fca.firm_rows("900507", FirmResource.NAMES) == [{"Previous Name": "TransferWise"}]


@pytest.mark.anyio
async def test_fca_unknown_firm_is_none(replay: Settings, tmp_path: Path) -> None:
    record(tmp_path, "fca", f"{FCA}/Firm/1", wrapped(None, "FSR-API-02-01-11", "Firm not found"))

    assert await FcaClient(replay).firm("1") is None


def test_fca_live_needs_email_and_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="VERIKYB_FCA_EMAIL and VERIKYB_FCA_API_KEY"):
        FcaClient(Settings(sources=SourceMode.LIVE, fixtures_dir=tmp_path, fca_email="a@b.c"))


@pytest.mark.anyio
async def test_fca_sends_its_auth_headers(tmp_path: Path) -> None:
    settings = Settings(sources=SourceMode.LIVE, fixtures_dir=tmp_path, fca_email="a@b.c", fca_api_key="k")
    fca = FcaClient(settings)
    sent = live_answering(fca, 200, wrapped(None, "FSR-API-04-01-11", "No search result found"))

    await fca.search_firms("x")

    assert (sent[0].headers["x-auth-email"], sent[0].headers["x-auth-key"]) == ("a@b.c", "k")


# GLEIF


def lei_item(lei: str, name: str, registered_as: str | None = None) -> dict[str, Any]:
    return {
        "id": lei,
        "attributes": {
            "entity": {
                "legalName": {"name": name},
                "jurisdiction": "GB",
                "registeredAs": registered_as,
                "legalAddress": {"country": "GB"},
                "status": "ACTIVE",
            },
            "registration": {"status": "ISSUED"},
        },
    }


@pytest.mark.anyio
async def test_gleif_finds_an_lei_by_company_number(replay: Settings, tmp_path: Path) -> None:
    item = lei_item("213800U4GNTXRFYZKG18", "WISE PAYMENTS LIMITED", "07209813")
    record(
        tmp_path,
        "gleif",
        f"{GLEIF}/lei-records?filter[entity.registeredAs]=07209813",
        {"data": [item]},
        content_type="application/vnd.api+json",
    )

    [found] = await GleifClient(replay).by_registration_number("07209813")

    assert (found.lei, found.legal_name, found.registered_as) == (
        "213800U4GNTXRFYZKG18",
        "WISE PAYMENTS LIMITED",
        "07209813",
    )
    assert (found.entity_status, found.registration_status) == ("ACTIVE", "ISSUED")


@pytest.mark.anyio
async def test_gleif_finds_nothing_for_an_unknown_number(replay: Settings, tmp_path: Path) -> None:
    record(tmp_path, "gleif", f"{GLEIF}/lei-records?filter[entity.registeredAs]=0", {"data": []})

    assert await GleifClient(replay).by_registration_number("0") == []


@pytest.mark.anyio
async def test_gleif_parents_and_their_absence(replay: Settings, tmp_path: Path) -> None:
    lei = "213800U4GNTXRFYZKG18"
    record(
        tmp_path,
        "gleif",
        f"{GLEIF}/lei-records/{lei}/direct-parent",
        {"data": lei_item("P1", "WISE PLC")},
    )
    record(tmp_path, "gleif", f"{GLEIF}/lei-records/{lei}/ultimate-parent", {"errors": []}, status=404)
    gleif = GleifClient(replay)

    parent = await gleif.direct_parent(lei)

    assert parent is not None
    assert parent.legal_name == "WISE PLC"
    assert await gleif.ultimate_parent(lei) is None  # none reported


@pytest.mark.anyio
async def test_gleif_search_without_records_is_unavailable(replay: Settings, tmp_path: Path) -> None:
    record(
        tmp_path,
        "gleif",
        f"{GLEIF}/lei-records?filter[entity.legalName]=X",
        {"errors": []},
        status=404,
    )

    with pytest.raises(SourceUnavailableError, match="gleif"):
        await GleifClient(replay).by_name("X")


@pytest.mark.anyio
async def test_gleif_answer_without_a_list_of_records_is_unavailable(replay: Settings, tmp_path: Path) -> None:
    url = f"{GLEIF}/lei-records?filter[entity.legalName]=Y"
    record(tmp_path, "gleif", url, {"errors": [{"title": "maintenance"}]})

    with pytest.raises(SourceUnavailableError, match="gleif"):
        await GleifClient(replay).by_name("Y")


# EDGAR


@pytest.mark.anyio
async def test_edgar_company_pads_the_cik(replay: Settings, tmp_path: Path) -> None:
    submissions = {
        "name": "PayPal Holdings, Inc.",
        "tickers": ["PYPL"],
        "exchanges": ["Nasdaq", ""],
        "sic": "7389",
        "sicDescription": "Services-Business Services, NEC",
        "stateOfIncorporation": "DE",
    }
    record(tmp_path, "edgar", f"{SEC}/submissions/CIK0001633917.json", submissions)

    company = await EdgarClient(replay).company("1633917")

    assert company is not None
    assert (company.cik, company.name, company.tickers) == (
        "0001633917",
        "PayPal Holdings, Inc.",
        ["PYPL"],
    )
    assert company.exchanges == ["Nasdaq"]
    assert company.state_of_incorporation == "DE"


@pytest.mark.anyio
async def test_edgar_facts_by_concept_keep_exact_values(replay: Settings, tmp_path: Path) -> None:
    revenue = {
        "start": "2024-01-01",
        "end": "2024-12-31",
        "val": 31797000000,
        "form": "10-K",
        "fp": "FY",
        "filed": "2025-02-04",
        "accn": "0001633917-25-000010",
    }
    eps = {**revenue, "val": 4.1, "accn": "0001633917-25-000011"}
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [revenue]}},
                "EarningsPerShareBasic": {"units": {"USD/shares": [eps]}},
            }
        }
    }
    record(tmp_path, "edgar", f"{SEC}/api/xbrl/companyfacts/CIK0001633917.json", facts)
    edgar = EdgarClient(replay)

    found = await edgar.facts("1633917", ["Revenues", "Assets"])
    per_share = await edgar.facts("1633917", ["EarningsPerShareBasic"], unit="USD/shares")

    [fact] = found["Revenues"]
    assert (fact.value, fact.start, fact.form) == (Decimal(31797000000), date(2024, 1, 1), "10-K")
    assert found["Assets"] == []  # never reported
    assert per_share["EarningsPerShareBasic"][0].value == Decimal("4.1")


def test_edgar_live_needs_a_user_agent_with_an_email(tmp_path: Path) -> None:
    live = {"sources": SourceMode.LIVE, "fixtures_dir": tmp_path}
    with pytest.raises(ValueError, match="VERIKYB_SEC_USER_AGENT"):
        EdgarClient(Settings(**live))
    with pytest.raises(ValueError, match="email"):
        EdgarClient(Settings(**live, sec_user_agent="VeriKYB"))


@pytest.mark.anyio
async def test_edgar_user_agent_comes_from_settings(tmp_path: Path) -> None:
    settings = Settings(sources=SourceMode.LIVE, fixtures_dir=tmp_path, sec_user_agent="Analyst a@b.example")
    edgar = EdgarClient(settings)
    sent = live_answering(edgar, 404, {})

    await edgar.company("1")

    assert sent[0].headers["user-agent"] == "Analyst a@b.example"


def test_edgar_replays_without_a_user_agent(replay: Settings) -> None:
    EdgarClient(replay)  # replay never calls the SEC, so it needs no contact details


# News (GDELT)


def gdelt_url(name: str) -> str:
    query = httpx.QueryParams(
        {
            "query": f'"{name}"',
            "mode": "artlist",
            "format": "json",
            "maxrecords": "75",
            "sort": "datedesc",
        }
    )
    return f"{GDELT}/doc/doc?{query}"


@pytest.mark.anyio
async def test_news_search_parses_articles(replay: Settings, tmp_path: Path) -> None:
    article = {
        "url": "https://news.example.test/a",
        "title": "Quillon Trade Finance fined",
        "seendate": "20261001T120000Z",
        "domain": "news.example.test",
        "language": "English",
        "sourcecountry": "United Kingdom",
    }
    record(tmp_path, "gdelt", gdelt_url("Quillon Trade Finance Ltd"), {"articles": [article]})

    [found] = await GdeltNews(replay).search("Quillon Trade Finance Ltd")

    assert found.title == "Quillon Trade Finance fined"
    assert found.seen_at == datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert found.source_country == "United Kingdom"


@pytest.mark.anyio
async def test_news_with_no_articles_is_an_answer(replay: Settings, tmp_path: Path) -> None:
    record(tmp_path, "gdelt", gdelt_url("Clean Co Ltd"), {})

    assert await GdeltNews(replay).search("Clean Co Ltd") == []


@pytest.mark.anyio
async def test_a_quote_in_a_name_cannot_break_the_exact_phrase(replay: Settings, tmp_path: Path) -> None:
    record(tmp_path, "gdelt", gdelt_url("The Best Co"), {})

    assert await GdeltNews(replay).search('The "Best" Co') == []  # asked as "The Best Co"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body", "text"),
    [
        (404, {}, None),
        (200, None, "Please limit requests to one every 5 seconds"),
        (200, ["not", "an", "answer"], None),
        (503, {}, None),
    ],
    ids=["404", "text-error", "wrong-json", "down"],
)
async def test_news_that_cannot_answer_is_unavailable_never_clean(tmp_path: Path, status: int, body: Any, text: str | None) -> None:
    news = GdeltNews(Settings(sources=SourceMode.LIVE, fixtures_dir=tmp_path))
    live_answering(news, status, body, text)

    with pytest.raises(SourceUnavailableError):
        await news.search("Any Co Ltd")


def test_gdelt_fits_the_news_search_interface(replay: Settings) -> None:
    search: NewsSearch = GdeltNews(replay)
    assert search is not None


@pytest.mark.anyio
async def test_every_source_replays_offline_and_never_guesses(replay: Settings) -> None:
    """With nothing recorded, each source says it is unavailable rather than calling out."""
    calls = [
        FcaClient(replay).search_firms("x"),
        GleifClient(replay).by_name("x"),
        EdgarClient(replay).company("1"),
        GdeltNews(replay).search("x"),
    ]
    for call in calls:
        with pytest.raises(SourceUnavailableError, match="no recorded response"):
            await call
