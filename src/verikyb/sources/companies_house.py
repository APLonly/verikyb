"""Companies House public data API: the UK registry, plus the filings behind the accounts parser.

Authentication is HTTP basic auth with the API key as the username and an empty password.
"""

import re
from datetime import date
from typing import Any, ClassVar

from verikyb.models import Address, DomainModel, PartyKind
from verikyb.settings import Settings
from verikyb.sources.http import SourceClient, required_in_live_mode
from verikyb.sources.registry import RegistryCompany, RegistryOfficer, RegistryOwner

XHTML = "application/xhtml+xml"
PAGE_SIZE = 100  # the most Companies House returns per page for officers and PSCs
MAX_PAGES = 20  # a guard against a total that never adds up
_SHARE_BAND = re.compile(r"ownership-of-shares-(\d+)-to-(\d+)-percent")
_SHARE_FLOOR = re.compile(r"ownership-of-shares-more-than-(\d+)-percent")


class Filing(DomainModel):
    transaction_id: str
    filed_on: date
    category: str
    description: str
    made_up_to: date | None = None
    document_url: str | None = None


class Appointment(DomainModel):
    """Another company an officer serves."""

    company_number: str
    company_name: str
    company_status: str | None = None
    role: str
    appointed_on: date | None = None
    resigned_on: date | None = None


class CompaniesHouseClient(SourceClient):
    name = "companies_house"
    base_url = "https://api.company-information.service.gov.uk"
    rate_limit: ClassVar[tuple[int, float]] = (600, 300.0)  # Companies House: 600 per 5 minutes
    jurisdiction = "GB"

    def __init__(self, settings: Settings) -> None:
        key = settings.companies_house_api_key
        required_in_live_mode(settings, "VERIKYB_COMPANIES_HOUSE_API_KEY", key)
        super().__init__(settings, auth=(key.get_secret_value(), "") if key else None)

    async def search(self, name: str) -> list[RegistryCompany]:
        data = await self._get_json("/search/companies", {"q": name, "items_per_page": "20"})
        return [_company(item) for item in (data or {}).get("items", [])]

    async def company(self, number: str) -> RegistryCompany | None:
        data = await self._get_json(f"/company/{number}")
        return _company(data) if data else None

    async def officers(self, number: str) -> list[RegistryOfficer]:
        """Current and resigned officers, every page of them."""
        return [_officer(item) for item in await self._all_items(f"/company/{number}/officers")]

    async def owners(self, number: str) -> list[RegistryOwner]:
        """Persons with significant control, current and ceased. Statements (for example "no
        registrable person") are not owners and are left out."""
        items = await self._all_items(f"/company/{number}/persons-with-significant-control")
        return [owner for item in items if (owner := _owner(item))]

    async def officer_appointments(self, officer_id: str) -> list[Appointment]:
        """Every company an officer serves: how a director shared with another company shows."""
        data = await self._get_json(f"/officers/{officer_id}/appointments", {"items_per_page": "50"})
        return [_appointment(item) for item in (data or {}).get("items", [])]

    async def filings(self, number: str, category: str = "accounts") -> list[Filing]:
        """Newest first, as Companies House returns them."""
        data = await self._get_json(f"/company/{number}/filing-history", {"category": category, "items_per_page": "25"})
        return [_filing(item) for item in (data or {}).get("items", [])]

    async def accounts_document(self, filing: Filing) -> str | None:
        """The iXBRL accounts behind a filing; None for paper and PDF-only filings."""
        if filing.document_url is None:
            return None
        metadata = await self._get_json(filing.document_url)
        if not metadata or XHTML not in metadata.get("resources", {}):
            return None
        response = await self._get(f"{filing.document_url}/content", accept=XHTML)
        return response.text if response else None

    async def outstanding_charges(self, number: str) -> int:
        """Charges (secured loans) not yet satisfied, in full or in part."""
        data = await self._get_json(f"/company/{number}/charges")
        items = (data or {}).get("items", [])
        return sum(1 for item in items if item.get("status") in {"outstanding", "part-satisfied"})

    async def insolvency_cases(self, number: str) -> list[str]:
        """The type of each insolvency case; none when Companies House answers 404."""
        data = await self._get_json(f"/company/{number}/insolvency")
        return [case.get("type", "unknown") for case in (data or {}).get("cases", [])]

    async def _all_items(self, url: str) -> list[dict[str, Any]]:
        """Every item of a paged list; nothing when the company does not exist (404)."""
        items: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            params = {"items_per_page": str(PAGE_SIZE), "start_index": str(len(items))}
            page = await self._get_json(url, params) or {}
            batch = page.get("items", [])
            items.extend(batch)
            if not batch or len(items) >= page.get("total_results", 0):
                break
        return items


def _company(raw: dict[str, Any]) -> RegistryCompany:
    """From a company profile or a search result, which name a few fields differently."""
    accounts = raw.get("accounts", {})
    return RegistryCompany(
        number=raw["company_number"],
        name=raw.get("company_name") or raw["title"],
        jurisdiction="GB",
        status=raw.get("company_status", "unknown"),
        incorporated_on=raw.get("date_of_creation"),
        legal_form=raw.get("type") or raw.get("company_type"),
        address=_address(raw.get("registered_office_address") or raw.get("address")),
        activity_codes=raw.get("sic_codes", []),
        previous_names=[previous["name"] for previous in raw.get("previous_company_names", [])],
        last_accounts_made_up_to=accounts.get("last_accounts", {}).get("made_up_to"),
        accounts_next_due=accounts.get("next_due"),
        accounts_overdue=accounts.get("overdue", False),
        confirmation_statement_overdue=raw.get("confirmation_statement", {}).get("overdue", False),
        has_charges=raw.get("has_charges", False),
        has_insolvency_history=raw.get("has_insolvency_history", False),
    )


def _address(raw: dict[str, Any] | None) -> Address | None:
    if not raw:
        return None
    first = " ".join(part for part in (raw.get("premises"), raw.get("address_line_1")) if part)
    lines = [line for line in (first, raw.get("address_line_2")) if line]
    if not lines:
        return None
    return Address(
        lines=lines,
        locality=raw.get("locality"),
        postal_code=raw.get("postal_code"),
        country="GB",
    )


def _officer(raw: dict[str, Any]) -> RegistryOfficer:
    born = raw.get("date_of_birth", {})
    appointments = raw.get("links", {}).get("officer", {}).get("appointments", "")
    return RegistryOfficer(
        name=raw["name"],
        role=raw.get("officer_role", "unknown"),
        officer_id=appointments.split("/")[2] if appointments else None,  # /officers/<id>/...
        appointed_on=raw.get("appointed_on"),
        resigned_on=raw.get("resigned_on"),
        nationality=raw.get("nationality"),
        country_of_residence=raw.get("country_of_residence"),
        birth_year=born.get("year"),
        birth_month=born.get("month"),
    )


def _owner(raw: dict[str, Any]) -> RegistryOwner | None:
    kind = raw.get("kind", "")
    if kind.startswith(("individual", "super-secure")):
        party = PartyKind.PERSON
    elif kind.startswith(("corporate-entity", "legal-person")):
        party = PartyKind.ENTITY
    else:
        return None
    natures = raw.get("natures_of_control", [])
    low, high = _share_band(natures)
    identification = raw.get("identification", {})
    return RegistryOwner(
        name=raw.get("name", "Protected person"),  # super-secure PSCs carry no name
        kind=party,
        min_pct=low,
        max_pct=high,
        controls=natures,
        country=raw.get("country_of_residence") or identification.get("country_registered"),
        registration_number=identification.get("registration_number"),
        notified_on=raw.get("notified_on"),
        ceased_on=raw.get("ceased_on"),
    )


def _share_band(natures: list[str]) -> tuple[float | None, float | None]:
    """PSC filings disclose bands such as `ownership-of-shares-25-to-50-percent`."""
    for nature in natures:
        if band := _SHARE_BAND.search(nature):
            return float(band[1]), float(band[2])
        if floor := _SHARE_FLOOR.search(nature):
            return float(floor[1]), 100.0
    return None, None


def _appointment(raw: dict[str, Any]) -> Appointment:
    company = raw.get("appointed_to", {})
    return Appointment(
        company_number=company["company_number"],
        company_name=company.get("company_name", ""),
        company_status=company.get("company_status"),
        role=raw.get("officer_role", "unknown"),
        appointed_on=raw.get("appointed_on"),
        resigned_on=raw.get("resigned_on"),
    )


def _filing(raw: dict[str, Any]) -> Filing:
    return Filing(
        transaction_id=raw["transaction_id"],
        filed_on=raw["date"],
        category=raw.get("category", ""),
        description=raw.get("description", ""),
        made_up_to=raw.get("description_values", {}).get("made_up_date"),
        document_url=raw.get("links", {}).get("document_metadata"),
    )
