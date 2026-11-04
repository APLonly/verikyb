"""The resolved entity behind a request: identity, people, ownership and financials."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from verikyb.models.common import CitedIds, CountryCode, DomainModel, RequestId


class Address(DomainModel):
    lines: list[str] = Field(min_length=1)
    locality: str | None = None
    postal_code: str | None = None
    country: CountryCode


class Customer(DomainModel):
    request_id: RequestId
    legal_name: str
    jurisdiction: CountryCode
    is_synthetic: bool = Field(description="Real firms show only factual data under an 'automated signals' disclaimer")
    company_number: str | None = None
    lei: str | None = Field(default=None, pattern=r"^[A-Z0-9]{18}[0-9]{2}$")
    frn: str | None = Field(default=None, pattern=r"^\d{6,7}$")  # FCA Firm Reference Number
    fca_status: str | None = None
    fca_permissions: list[str] = []
    trading_names: list[str] = []
    registered_address: Address | None = None
    incorporation_date: date | None = None
    legal_form: str | None = None
    sic_codes: list[str] = []
    website: str | None = None
    field_sources: dict[str, list[str]] = Field(
        default={},
        description="Field name -> evidence IDs, so every field can show its source",
    )
    last_refreshed: AwareDatetime | None = None

    @model_validator(mode="after")
    def _sources_name_real_fields(self) -> Self:
        unknown = set(self.field_sources) - set(type(self).model_fields)
        if unknown:
            raise ValueError(f"field_sources names unknown fields: {sorted(unknown)}")
        return self


class PersonRole(StrEnum):
    DIRECTOR = "director"
    SECRETARY = "secretary"
    PSC = "psc"  # person with significant control
    UBO = "ubo"  # ultimate beneficial owner, derived from the ownership graph
    SHAREHOLDER = "shareholder"
    CONTROLLED_FUNCTION = "controlled_function"  # FCA-approved individual


class Person(DomainModel):
    person_id: str
    full_name: str
    roles: list[PersonRole] = Field(min_length=1)
    nationality: str | None = None
    country_of_residence: str | None = None
    # Registries publish month and year of birth only; a full date of birth is never stored.
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    birth_month: int | None = Field(default=None, ge=1, le=12)
    appointed_on: date | None = None
    resigned_on: date | None = None
    evidence_ids: CitedIds


class PartyKind(StrEnum):
    ENTITY = "entity"
    PERSON = "person"


class OwnershipKind(StrEnum):
    SHARES = "shares"
    VOTING_RIGHTS = "voting_rights"
    SIGNIFICANT_CONTROL = "significant_control"  # e.g. right to appoint the board; no percentage


class OwnershipEdge(DomainModel):
    """`owner_id` holds between `min_pct` and `max_pct` of `owned_id`.

    A range, because UK PSC filings disclose bands (25-50%, 50-75%, 75-100%) rather than exact
    holdings; effective ownership multiplies the bounds along each path instead of guessing.
    """

    owner_id: str
    owner_kind: PartyKind
    owned_id: str
    kind: OwnershipKind
    min_pct: float | None = Field(default=None, ge=0, le=100)
    max_pct: float | None = Field(default=None, ge=0, le=100)
    evidence_ids: CitedIds

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if (self.min_pct is None) != (self.max_pct is None):
            raise ValueError("give both min_pct and max_pct, or neither")
        if self.min_pct is not None and self.max_pct is not None and self.min_pct > self.max_pct:
            raise ValueError("min_pct cannot exceed max_pct")
        return self


class Figure(DomainModel):
    value: Decimal
    source_element: str = Field(description="The filed element, e.g. uk-core:Turnover")
    evidence_id: str


class FinancialYear(DomainModel):
    """One reporting period. A figure the filing does not contain stays None: never estimated."""

    period_end: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    revenue: Figure | None = None
    operating_profit: Figure | None = None
    net_income: Figure | None = None
    total_assets: Figure | None = None
    total_liabilities: Figure | None = None
    equity: Figure | None = None
    cash: Figure | None = None
