"""FCA Financial Services Register API: firm look-ups, one entity per call."""

import re
from enum import StrEnum
from typing import Any, ClassVar

from verikyb.models import DomainModel
from verikyb.settings import Settings
from verikyb.sources.http import SourceClient, SourceUnavailableError, required_in_live_mode

_POSTCODE_SUFFIX = re.compile(r"\s*\(Postcode:[^)]*\)$")
_NOT_FOUND = re.compile(r"no search result|not found", re.IGNORECASE)


class FirmResource(StrEnum):
    NAMES = "Names"
    ADDRESSES = "Address"
    INDIVIDUALS = "Individuals"
    APPOINTED_REPRESENTATIVES = "AR"
    REGULATORS = "Regulators"
    REQUIREMENTS = "Requirements"
    DISCIPLINARY_HISTORY = "DisciplinaryHistory"


class FcaSearchHit(DomainModel):
    frn: str
    name: str
    status: str


class FcaFirm(DomainModel):
    frn: str
    name: str
    status: str
    status_effective_date: str | None = None
    business_type: str | None = None
    companies_house_number: str | None = None  # checks the record is the same company


class FcaClient(SourceClient):
    name = "fca"
    base_url = "https://register.fca.org.uk/services/V0.1"
    rate_limit: ClassVar[tuple[int, float]] = (50, 10.0)  # the FCA's published limit

    def __init__(self, settings: Settings) -> None:
        email, key = settings.fca_email, settings.fca_api_key
        required_in_live_mode(settings, "VERIKYB_FCA_EMAIL and VERIKYB_FCA_API_KEY", email, key)
        headers = {"Accept": "application/json"}
        if email and key:
            headers |= {"X-Auth-Email": email, "X-Auth-Key": key.get_secret_value()}
        super().__init__(settings, headers=headers)

    async def search_firms(self, name: str) -> list[FcaSearchHit]:
        """Firms only (not individuals), with the postcode suffix taken off their names."""
        rows = await self._data("/Search", {"q": name, "type": "firm"})
        return [
            FcaSearchHit(
                frn=str(row["Reference Number"]),
                name=_POSTCODE_SUFFIX.sub("", row["Name"]),
                status=row.get("Status", ""),
            )
            for row in rows
            if row.get("Type of business or Individual") == "Firm"
        ]

    async def firm(self, frn: str) -> FcaFirm | None:
        rows = await self._data(f"/Firm/{frn}")
        if not rows:
            return None
        row = rows[0]
        return FcaFirm(
            frn=str(row["FRN"]),
            name=row["Organisation Name"],
            status=row.get("Status", ""),
            status_effective_date=row.get("Status Effective Date"),
            business_type=row.get("Business Type"),
            companies_house_number=row.get("Companies House Number") or None,
        )

    async def permissions(self, frn: str) -> list[str]:
        """Permission names; each row maps a permission to its limitations."""
        return [permission for row in await self._data(f"/Firm/{frn}/Permissions") for permission in row]

    async def firm_rows(self, frn: str, resource: FirmResource) -> list[dict[str, Any]]:
        return await self._data(f"/Firm/{frn}/{resource}")

    async def _data(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        payload = await self._get_json(path, params)
        if payload is None:  # a 404 from the register itself
            return []
        message = str(payload.get("Message") or "")
        if _NOT_FOUND.search(message):
            return []
        if not message.casefold().startswith("ok"):
            status = payload.get("Status", "no status")
            raise SourceUnavailableError(f"fca answered {status}: {message or 'no message'}")
        data = payload.get("Data") or []
        return data if isinstance(data, list) else [data]
