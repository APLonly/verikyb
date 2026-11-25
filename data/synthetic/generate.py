"""Builds the synthetic half of the demo set from companies.yaml, without network access:

- API-shaped recordings under data/fixtures, so the same source clients replay synthetic and real companies alike (Companies House, FCA, GLEIF, GDELT)
- the synthetic sanctions list, the mock India registry and the prior-review snapshot
- Indian documents, as a PDF and a scanned-looking PNG, under data/synthetic/documents
- ground truth for the evaluations: data/synthetic/ground_truth.json

Run it with `make data`.
"""

import base64
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from documents import render
from ixbrl import accounts_ixbrl
from spec import IndiaCompany, Person, SpecAddress, UkCompany, World

from verikyb.models import Address, PartyKind
from verikyb.sources.companies_house import PAGE_SIZE, XHTML, CompaniesHouseClient
from verikyb.sources.fca import FcaClient
from verikyb.sources.gleif import GleifClient
from verikyb.sources.http import Recording, RecordingStore
from verikyb.sources.mock_india import INDIA_RECORDS, IndiaRecord
from verikyb.sources.news import GdeltNews
from verikyb.sources.registry import RegistryCompany, RegistryOfficer, RegistryOwner
from verikyb.sources.sanctions import write_entries

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures"
DOCUMENTS = HERE / "documents"
RECORDED_AT = datetime(2026, 10, 1, tzinfo=UTC)  # fixed, so re-runs are byte-identical
DOCUMENT_API = "https://document-api.company-information.service.gov.uk"
PDF_ONLY_FILING = {"made_up_to": date(2025, 3, 31), "filed_on": date(2025, 11, 20)}
CH, FCA, GLEIF, GDELT = CompaniesHouseClient, FcaClient, GleifClient, GdeltNews
# The client reads officers and owners page by page; a synthetic company fits on the first.
FIRST_PAGE = {"items_per_page": str(PAGE_SIZE), "start_index": "0"}


def main() -> None:
    world = World.model_validate(yaml.safe_load((HERE / "companies.yaml").read_text("utf-8")))
    _remove_synthetic_recordings()
    by_number = {company.number: company for company in world.uk_companies}
    for company in world.uk_companies:
        _companies_house(company, world.people, by_number)
        _gleif(company, by_number)
        if company.customer:
            _fca(company)
            _news(company.name, company)
    for key, person in world.people.items():
        _appointments(key, person, [c for c in world.uk_companies if key in c.directors])

    DOCUMENTS.mkdir(parents=True, exist_ok=True)
    documents = []
    for india in world.india_companies:
        _record(
            GLEIF.name,
            f"{GLEIF.base_url}/lei-records",
            {"data": []},
            {"filter[entity.registeredAs]": india.cin},
        )
        _news(india.name, None)
        documents += [render(india, doc_type, DOCUMENTS) for doc_type in india.documents]
    _write_json(
        FIXTURES / "india_registry" / "companies.json",
        INDIA_RECORDS.dump_python([_india_record(c) for c in world.india_companies], mode="json", exclude_defaults=True),
    )
    write_entries(world.sanctions_entries(), FIXTURES / "sanctions" / "synthetic.jsonl")
    _write_json(
        FIXTURES / "prior_reviews.json",
        [
            {
                "company_number": c.number,
                "reviewed_on": c.fca.last_review.reviewed_on,
                "fca_permissions": c.fca.last_review.permissions,
            }
            for c in world.uk_companies
            if c.fca
        ],
    )
    _write_json(HERE / "ground_truth.json", _ground_truth(world, documents))
    print(f"Synthetic data: {len(world.uk_companies)} UK and {len(world.india_companies)} Indian companies, {len(documents)} documents, {len(world.sanctions)} list entries.")


def _companies_house(company: UkCompany, people: dict[str, Person], by_number: dict[str, UkCompany]) -> None:
    base, number = CH.base_url, company.number
    _record(
        CH.name,
        f"{base}/search/companies",
        {
            "kind": "search#companies",
            "total_results": 1,
            "items": [
                {
                    "kind": "searchresults#company",
                    "company_number": number,
                    "title": company.name.upper(),
                    "company_status": "active",
                    "company_type": "ltd",
                    "date_of_creation": company.incorporated_on,
                    "address": _ch_address(company.address),
                }
            ],
        },
        {"q": company.name, "items_per_page": "20"},
    )
    filings = _filings(company)
    _record(
        CH.name,
        f"{base}/company/{number}",
        {
            "company_number": number,
            "company_name": company.name,
            "company_status": "active",
            "type": "ltd",
            "jurisdiction": "england-wales",
            "date_of_creation": company.incorporated_on,
            "registered_office_address": _ch_address(company.address),
            "sic_codes": company.sic_codes,
            "accounts": {
                "next_due": company.accounts_next_due,
                "overdue": company.accounts_overdue,
                "last_accounts": {"made_up_to": filings[0]["made_up_to"]},
            },
            "confirmation_statement": {"next_due": date(2027, 1, 31), "overdue": False},
            "has_charges": False,
            "has_insolvency_history": False,
        },
    )
    _record(
        CH.name,
        f"{base}/company/{number}/officers",
        {
            "kind": "officer-list",
            "total_results": len(company.directors),
            "items": [_ch_officer(key, people[key], company.incorporated_on) for key in company.directors],
        },
        FIRST_PAGE,
    )
    _record(
        CH.name,
        f"{base}/company/{number}/persons-with-significant-control",
        {
            "total_results": len(company.owners),
            "items": [
                _ch_psc(
                    holding.min_pct,
                    holding.max_pct,
                    company.incorporated_on,
                    people[holding.person] if holding.person else None,
                    by_number[holding.company] if holding.company else None,
                )
                for holding in company.owners
            ],
        },
        FIRST_PAGE,
    )
    _record(
        CH.name,
        f"{base}/company/{number}/filing-history",
        {
            "filing_history_status": "filing-history-available",
            "total_count": len(filings),
            "items": [
                {
                    "transaction_id": filing["transaction_id"],
                    "category": "accounts",
                    "type": "AA",
                    "date": filing["filed_on"],
                    "description": filing["description"],
                    "description_values": {"made_up_date": filing["made_up_to"]},
                    "links": {"document_metadata": filing["document_url"]},
                }
                for filing in filings
            ],
        },
        {"category": "accounts", "items_per_page": "25"},
    )
    for filing in filings:
        resources = {"application/pdf": {"content_length": 48211}}
        if filing["ixbrl"]:
            resources[XHTML] = {"content_length": len(filing["ixbrl"])}
            _record(CH.name, f"{filing['document_url']}/content", filing["ixbrl"], content_type=XHTML)
        _record(
            CH.name,
            filing["document_url"],
            {
                "company_number": number,
                "category": "accounts",
                "significant_date": filing["made_up_to"],
                "links": {"document": f"{filing['document_url']}/content"},
                "resources": resources,
            },
        )
    _record(
        CH.name,
        f"{base}/company/{number}/charges",
        {
            "total_count": 0,
            "unfiltered_count": 0,
            "satisfied_count": 0,
            "items": [],
        },
    )
    _record(
        CH.name,
        f"{base}/company/{number}/insolvency",
        {
            "errors": [{"type": "ch:service", "error": "company-insolvencies-not-found"}],
        },
        status=404,
    )


def _filings(company: UkCompany) -> list[dict[str, Any]]:
    """Newest first. A company without accounts in the spec gets one PDF-only filing, which
    exercises the path where nothing machine-readable was filed."""
    if not company.accounts:
        return [
            {
                **PDF_ONLY_FILING,
                "transaction_id": _token(f"{company.number}-0"),
                "description": "accounts-with-accounts-type-micro-entity",
                "document_url": f"{DOCUMENT_API}/document/{_token(company.number + 'doc0')}",
                "ixbrl": None,
            }
        ]
    return [
        {
            "transaction_id": _token(f"{company.number}-{index}"),
            "filed_on": accounts.filed_on,
            "made_up_to": accounts.years[0].period_end,
            "description": "accounts-with-accounts-type-small" if accounts.balance_sheet_only else "accounts-with-accounts-type-full",
            "document_url": f"{DOCUMENT_API}/document/{_token(f'{company.number}doc{index}')}",
            "ixbrl": accounts_ixbrl(company, accounts),
        }
        for index, accounts in enumerate(company.accounts)
    ]


def _ch_officer(key: str, person: Person, appointed_on: date) -> dict[str, Any]:
    forenames, _, surname = person.name.rpartition(" ")
    return {
        "name": f"{surname.upper()}, {forenames}",
        "officer_role": "director",
        "appointed_on": appointed_on,
        "nationality": person.nationality,
        "country_of_residence": person.residence,
        "occupation": "Company Director",
        "date_of_birth": {"month": person.birth_month, "year": person.birth_year},
        "links": {"officer": {"appointments": f"/officers/{_token(key)}/appointments"}},
    }


def _ch_psc(
    min_pct: float,
    max_pct: float,
    notified_on: date,
    person: Person | None,
    company: UkCompany | None,
) -> dict[str, Any]:
    band = f"{min_pct:g}-to-{max_pct:g}-percent"
    common = {
        "natures_of_control": [f"ownership-of-shares-{band}", f"voting-rights-{band}"],
        "notified_on": notified_on,
    }
    if person:
        return {
            **common,
            "kind": "individual-person-with-significant-control",
            "name": person.name,
            "nationality": person.nationality,
            "country_of_residence": person.residence,
            "date_of_birth": {"month": person.birth_month, "year": person.birth_year},
        }
    assert company is not None
    return {
        **common,
        "kind": "corporate-entity-person-with-significant-control",
        "name": company.name,
        "identification": {
            "legal_authority": "Companies Act 2006",
            "legal_form": "Private Limited Company",
            "country_registered": "England",
            "registration_number": company.number,
        },
    }


def _appointments(key: str, person: Person, companies: list[UkCompany]) -> None:
    _record(
        CH.name,
        f"{CH.base_url}/officers/{_token(key)}/appointments",
        {
            "kind": "personal-appointment",
            "name": person.name,
            "total_results": len(companies),
            "items": [
                {
                    "appointed_to": {
                        "company_number": c.number,
                        "company_name": c.name,
                        "company_status": "active",
                    },
                    "officer_role": "director",
                    "appointed_on": c.incorporated_on,
                }
                for c in companies
            ],
        },
        {"items_per_page": "50"},
    )


def _gleif(company: UkCompany, by_number: dict[str, UkCompany]) -> None:
    lei_records = f"{GLEIF.base_url}/lei-records"
    found = [_lei_item(company)] if company.lei else []
    _record(GLEIF.name, lei_records, {"data": found}, {"filter[entity.registeredAs]": company.number})
    if not company.lei:
        return
    _record(GLEIF.name, f"{lei_records}/{company.lei}", {"data": _lei_item(company)})
    for relation, parent in (
        ("direct-parent", company.gleif_direct_parent),
        ("ultimate-parent", company.gleif_ultimate_parent),
    ):
        url = f"{lei_records}/{company.lei}/{relation}"
        if parent:
            _record(GLEIF.name, url, {"data": _lei_item(by_number[parent])})
        else:
            _record(
                GLEIF.name,
                url,
                {"errors": [{"status": "404", "title": "Not Found"}]},
                status=404,
                content_type="application/vnd.api+json",
            )


def _lei_item(company: UkCompany) -> dict[str, Any]:
    return {
        "type": "lei-records",
        "id": company.lei,
        "attributes": {
            "lei": company.lei,
            "entity": {
                "legalName": {"name": company.name, "language": "en"},
                "legalAddress": {
                    "addressLines": company.address.lines,
                    "city": company.address.locality,
                    "postalCode": company.address.postal_code,
                    "country": "GB",
                },
                "registeredAs": company.number,
                "jurisdiction": "GB",
                "status": "ACTIVE",
            },
            "registration": {"status": "ISSUED"},
        },
    }


def _fca(company: UkCompany) -> None:
    search = f"{FCA.base_url}/Search"
    params = {"q": company.name, "type": "firm"}
    if company.fca is None:
        _record(
            FCA.name,
            search,
            {
                "Status": "FSR-API-04-01-11",
                "ResultInfo": None,
                "Message": "No search result found",
                "Data": None,
            },
            params,
        )
        return
    frn, firm = company.fca.frn, f"{FCA.base_url}/Firm/{company.fca.frn}"
    _record(
        FCA.name,
        search,
        {
            "Status": "FSR-API-04-01-00",
            "Message": "Ok. Search successful",
            "ResultInfo": {"page": "1", "per_page": "20", "total_count": "1"},
            "Data": [
                {
                    "Name": f"{company.name} (Postcode: {company.address.postal_code})",
                    "Reference Number": frn,
                    "Status": company.fca.status,
                    "Type of business or Individual": "Firm",
                    "URL": firm,
                }
            ],
        },
        params,
    )
    _record(
        FCA.name,
        firm,
        {
            "Status": "FSR-API-SYNTHETIC",
            "ResultInfo": None,
            "Message": "Ok",
            "Data": [
                {
                    "Organisation Name": company.name,
                    "FRN": frn,
                    "Status": company.fca.status,
                    "Status Effective Date": f"{company.fca.status_effective_date:%d/%m/%Y}",
                    "Business Type": "Regulated",
                    "Companies House Number": company.number,
                }
            ],
        },
    )
    _record(
        FCA.name,
        f"{firm}/Permissions",
        {
            "Status": "FSR-API-SYNTHETIC",
            "ResultInfo": None,
            "Message": "Ok",
            "Data": [{permission: [{"Limitation": ["Not applicable"]}]} for permission in company.fca.permissions],
        },
    )


def _news(name: str, company: UkCompany | None) -> None:
    articles = [
        {
            "url": a.url,
            "url_mobile": "",
            "title": a.title,
            "seendate": f"{a.seen_at:%Y%m%dT%H%M%SZ}",
            "socialimage": "",
            "domain": a.domain,
            "language": "English",
            "sourcecountry": "United Kingdom",
        }
        for a in (company.news if company else [])
    ]
    _record(
        GDELT.name,
        f"{GDELT.base_url}/doc/doc",
        {"articles": articles} if articles else {},
        {
            "query": f'"{name}"',
            "mode": "artlist",
            "format": "json",
            "maxrecords": "75",
            "sort": "datedesc",
        },
    )


def _india_record(company: IndiaCompany) -> IndiaRecord:
    address = company.address
    return IndiaRecord(
        company=RegistryCompany(
            number=company.cin,
            name=company.name,
            jurisdiction="IN",
            status="Active",
            incorporated_on=company.incorporated_on,
            legal_form="Private Limited Company",
            address=Address(
                lines=address.lines,
                locality=address.locality,
                postal_code=address.postal_code,
                country="IN",
            ),
            activity_codes=company.nic_codes,
        ),
        officers=[
            RegistryOfficer(
                name=d.name,
                role="Director",
                officer_id=d.din,
                appointed_on=d.appointed_on,
                nationality="Indian",
            )
            for d in company.directors
        ],
        owners=[
            RegistryOwner(
                name=s.name,
                kind=PartyKind.PERSON,
                min_pct=s.pct,
                max_pct=s.pct,
                controls=[f"{s.shares} equity shares"],
                country="India",
            )
            for s in company.shareholders
        ],
    )


def _ground_truth(world: World, documents: list[dict[str, Any]]) -> dict[str, Any]:
    uk = [
        {
            "jurisdiction": "GB",
            "number": c.number,
            "name": c.name,
            "scenario": c.scenario,
            "expected": c.expected,
            "reported_accounts": list({year.period_end: year.model_dump() for a in c.accounts for year in a.years}.values()),
        }
        for c in world.uk_companies
        if c.customer
    ]
    india = [
        {
            "jurisdiction": "IN",
            "number": c.cin,
            "name": c.name,
            "scenario": c.scenario,
            "expected": c.expected,
        }
        for c in world.india_companies
    ]
    return {
        "source": "data/synthetic/companies.yaml",
        "companies": uk + india,
        "documents": documents,
    }


def _record(
    source: str,
    url: str,
    body: Any,
    params: dict[str, str] | None = None,
    status: int = 200,
    content_type: str = "application/json",
) -> None:
    """Saves a response exactly where the source client will look it up."""
    recording = Recording(
        url=str(httpx.URL(url, params=params)),
        status=status,
        content_type=content_type,
        recorded_at=RECORDED_AT,
        synthetic=True,
        body=json.loads(json.dumps(body, default=str)),
    )
    RecordingStore(FIXTURES / source).save(recording)


def _remove_synthetic_recordings() -> None:
    """Drops recordings from earlier runs, so a renamed company leaves nothing stale behind."""
    for path in FIXTURES.glob("*/*.json"):
        data = json.loads(path.read_text("utf-8"))
        if isinstance(data, dict) and data.get("synthetic"):
            path.unlink()


def _ch_address(address: SpecAddress) -> dict[str, str]:
    lines = {f"address_line_{n}": line for n, line in enumerate(address.lines, start=1)}
    return {
        **lines,
        "locality": address.locality,
        "postal_code": address.postal_code,
        "country": "United Kingdom",
    }


def _token(text: str) -> str:
    """A stable ID in the style Companies House uses for officers and documents."""
    return base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).decode()[:27]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
