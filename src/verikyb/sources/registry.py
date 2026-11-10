"""The interface every company registry implements, so a new jurisdiction is one class.

Records keep the registry's own wording (status, roles, natures of control); the pipeline turns them into domain models and attaches evidence.
"""

from datetime import date
from typing import Protocol

from pydantic import Field

from verikyb.models import Address, CountryCode, DomainModel, PartyKind


class RegistryCompany(DomainModel):
    number: str
    name: str
    jurisdiction: CountryCode
    status: str
    incorporated_on: date | None = None
    legal_form: str | None = None
    address: Address | None = None
    activity_codes: list[str] = []  # SIC codes in the UK, NIC codes in India
    previous_names: list[str] = []
    last_accounts_made_up_to: date | None = None
    accounts_next_due: date | None = None
    accounts_overdue: bool = False
    confirmation_statement_overdue: bool = False
    has_charges: bool = False
    has_insolvency_history: bool = False


class RegistryOfficer(DomainModel):
    name: str
    role: str
    officer_id: str | None = None  # Companies House officer ID, or the Indian DIN
    appointed_on: date | None = None
    resigned_on: date | None = None
    nationality: str | None = None
    country_of_residence: str | None = None
    birth_year: int | None = None
    birth_month: int | None = None


class RegistryOwner(DomainModel):
    """A shareholder or person with significant control, with the share band it discloses."""

    name: str
    kind: PartyKind
    min_pct: float | None = Field(default=None, ge=0, le=100)
    max_pct: float | None = Field(default=None, ge=0, le=100)
    controls: list[str] = []  # the registry's own wording, e.g. natures of control
    country: str | None = None
    registration_number: str | None = None  # for a company owner
    notified_on: date | None = None
    ceased_on: date | None = None


class RegistryAdapter(Protocol):
    jurisdiction: str

    async def search(self, name: str) -> list[RegistryCompany]: ...

    async def company(self, number: str) -> RegistryCompany | None: ...

    async def officers(self, number: str) -> list[RegistryOfficer]: ...

    async def owners(self, number: str) -> list[RegistryOwner]: ...
