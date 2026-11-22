"""GLEIF API: LEI records and the direct and ultimate parents reported for them."""

from typing import Any, ClassVar

from verikyb.models import DomainModel
from verikyb.sources.http import SourceClient, SourceUnavailableError


class LeiRecord(DomainModel):
    lei: str
    legal_name: str
    jurisdiction: str | None = None  # e.g. GB, or US-DE for a Delaware company
    registered_as: str | None = None  # the local registry number, e.g. a company number
    country: str | None = None  # legal address country
    entity_status: str
    registration_status: str


class GleifClient(SourceClient):
    name = "gleif"
    base_url = "https://api.gleif.org/api/v1"
    rate_limit: ClassVar[tuple[int, float]] = (60, 60.0)

    async def by_registration_number(self, number: str) -> list[LeiRecord]:
        return await self._records({"filter[entity.registeredAs]": number})

    async def by_name(self, name: str) -> list[LeiRecord]:
        return await self._records({"filter[entity.legalName]": name})

    async def record(self, lei: str) -> LeiRecord | None:
        """None when GLEIF has no such LEI."""
        return await self._record(f"/lei-records/{lei}")

    async def direct_parent(self, lei: str) -> LeiRecord | None:
        """None when no parent is reported (GLEIF then holds a reporting exception instead)."""
        return await self._record(f"/lei-records/{lei}/direct-parent")

    async def ultimate_parent(self, lei: str) -> LeiRecord | None:
        return await self._record(f"/lei-records/{lei}/ultimate-parent")

    async def _records(self, params: dict[str, str]) -> list[LeiRecord]:
        payload = await self._get_json("/lei-records", params)
        if payload is None or not isinstance(payload.get("data"), list):
            raise SourceUnavailableError("gleif answered a search without a list of records")
        return [_lei_record(item) for item in payload["data"]]

    async def _record(self, path: str) -> LeiRecord | None:
        payload = await self._get_json(path)
        return _lei_record(payload["data"]) if payload and payload.get("data") else None


def _lei_record(item: dict[str, Any]) -> LeiRecord:
    attributes = item["attributes"]
    entity = attributes["entity"]
    return LeiRecord(
        lei=item["id"],
        legal_name=entity["legalName"]["name"],
        jurisdiction=entity.get("jurisdiction"),
        registered_as=entity.get("registeredAs"),
        country=entity.get("legalAddress", {}).get("country"),
        entity_status=entity.get("status", "UNKNOWN"),
        registration_status=attributes.get("registration", {}).get("status", "UNKNOWN"),
    )
