"""A stand-in for India's MCA registry, backed by synthetic records behind `RegistryAdapter`."""

from pydantic import TypeAdapter

from verikyb.models import DomainModel
from verikyb.settings import Settings
from verikyb.sources.http import SourceUnavailableError
from verikyb.sources.registry import RegistryCompany, RegistryOfficer, RegistryOwner


class IndiaRecord(DomainModel):
    company: RegistryCompany
    officers: list[RegistryOfficer] = []
    owners: list[RegistryOwner] = []


INDIA_RECORDS = TypeAdapter(list[IndiaRecord])


class MockIndiaRegistry:
    jurisdiction = "IN"

    def __init__(self, settings: Settings) -> None:
        path = settings.fixtures_dir / "india_registry" / "companies.json"
        if not path.exists():
            raise SourceUnavailableError(f"no India registry records at {path}; run `make data`")
        records = INDIA_RECORDS.validate_json(path.read_bytes())
        self._records = {record.company.number: record for record in records}

    async def search(self, name: str) -> list[RegistryCompany]:
        """Companies whose name contains `name`, ignoring case; nothing for an empty name."""
        needle = name.strip().casefold()
        if not needle:
            return []
        return [r.company for r in self._records.values() if needle in r.company.name.casefold()]

    async def company(self, number: str) -> RegistryCompany | None:
        record = self._records.get(number)
        return record.company if record else None

    async def officers(self, number: str) -> list[RegistryOfficer]:
        record = self._records.get(number)
        return record.officers if record else []

    async def owners(self, number: str) -> list[RegistryOwner]:
        record = self._records.get(number)
        return record.owners if record else []
