"""Typed view of companies.yaml, so a mistake in the synthetic world fails before anything is written. Expected outcomes are kept as written by hand; they are ground truth, not inputs."""

from datetime import date, datetime
from typing import Any, Literal, Self

from identifiers import CIN, COMPANY_PAN, DIN, FRN, UK_COMPANY_NUMBER, gstin_is_valid, lei_is_valid
from pydantic import BaseModel, ConfigDict, model_validator

from verikyb.sources.sanctions import SanctionsEntry


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Person(Spec):
    name: str
    nationality: str
    residence: str
    born: str  # YYYY-MM: registries publish month and year only

    @property
    def birth_year(self) -> int:
        return int(self.born[:4])

    @property
    def birth_month(self) -> int:
        return int(self.born[5:])


class Holding(Spec):
    person: str | None = None
    company: str | None = None
    min_pct: float
    max_pct: float

    @model_validator(mode="after")
    def _one_owner(self) -> Self:
        if (self.person is None) == (self.company is None):
            raise ValueError("a holding names a person or a company")
        return self


class SpecAddress(Spec):
    lines: list[str]
    locality: str
    postal_code: str


class AccountsYear(Spec):
    period_end: date
    turnover: int | None = None
    operating_profit: int | None = None
    profit_loss: int | None = None
    fixed_assets: int
    current_assets: int
    cash: int
    creditors: int
    net_assets: int

    @model_validator(mode="after")
    def _balances(self) -> Self:
        if self.fixed_assets + self.current_assets - self.creditors != self.net_assets:
            raise ValueError(f"balance sheet for {self.period_end} does not balance")
        return self


class Accounts(Spec):
    filed_on: date
    balance_sheet_only: bool = False
    years: list[AccountsYear]


class FcaReview(Spec):
    reviewed_on: date
    permissions: list[str]


class Fca(Spec):
    frn: str
    status: str
    status_effective_date: date
    permissions: list[str]
    last_review: FcaReview


class Article(Spec):
    title: str
    url: str
    domain: str
    seen_at: datetime


class UkCompany(Spec):
    number: str
    name: str
    customer: bool = False
    scenario: str | None = None
    incorporated_on: date
    sic_codes: list[str]
    address: SpecAddress
    directors: list[str]
    owners: list[Holding]
    lei: str | None = None
    gleif_direct_parent: str | None = None
    gleif_ultimate_parent: str | None = None
    accounts_next_due: date = date(2026, 12, 31)
    accounts_overdue: bool = False
    accounts: list[Accounts] = []
    fca: Fca | None = None
    news: list[Article] = []
    expected: dict[str, Any] = {}

    @model_validator(mode="after")
    def _identifiers(self) -> Self:
        if not UK_COMPANY_NUMBER.fullmatch(self.number):
            raise ValueError(f"{self.number}: not a Companies House number")
        if self.lei and not lei_is_valid(self.lei):
            raise ValueError(f"{self.lei}: LEI check digits are wrong")
        if self.fca and not FRN.fullmatch(self.fca.frn):
            raise ValueError(f"{self.fca.frn}: not an FRN")
        return self


class Director(Spec):
    name: str
    din: str
    appointed_on: date


class Shareholder(Spec):
    name: str
    document_name: str | None = None  # the spelling printed on the documents, if different
    shares: int
    pct: float


class Shareholding(Spec):
    as_at: date
    valid_until: date


DocumentType = Literal["certificate_of_incorporation", "gst_certificate", "pan_card", "shareholding_extract"]


class IndiaCompany(Spec):
    cin: str
    name: str
    scenario: str
    incorporated_on: date
    roc: str
    state: str
    gst_state_code: str
    pan: str
    gstin: str | None = None
    nic_codes: list[str]
    address: SpecAddress
    document_address: SpecAddress | None = None  # printed on documents, when it differs
    directors: list[Director]
    shareholders: list[Shareholder]
    shareholding: Shareholding
    documents: list[DocumentType]
    expected: dict[str, Any] = {}

    @model_validator(mode="after")
    def _identifiers(self) -> Self:
        if not CIN.fullmatch(self.cin) or not COMPANY_PAN.fullmatch(self.pan):
            raise ValueError(f"{self.name}: CIN or PAN is malformed")
        if self.gstin and not gstin_is_valid(self.gstin, self.pan, self.gst_state_code):
            raise ValueError(f"{self.gstin}: GSTIN does not match its PAN, state or checksum")
        if not all(DIN.fullmatch(director.din) for director in self.directors):
            raise ValueError(f"{self.name}: a DIN is malformed")
        return self


class World(Spec):
    people: dict[str, Person]
    uk_companies: list[UkCompany]
    india_companies: list[IndiaCompany]
    sanctions: list[dict[str, Any]]

    def sanctions_entries(self) -> list[SanctionsEntry]:
        return [SanctionsEntry.model_validate({"list_name": "synthetic", **e}) for e in self.sanctions]
