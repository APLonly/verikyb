"""Sanctions and PEP lists, parsed into one entry shape for screening."""

import csv
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path

from verikyb.models import DomainModel, PartyKind, ScreeningList

OFAC_EMPTY = "-0-"
OFAC_PARTY_TYPES = {"individual": PartyKind.PERSON, OFAC_EMPTY: PartyKind.ENTITY}  # not vessels
_OFAC_DOB = re.compile(r"DOB ([^;]+)")  # "DOB 10 Dec 1948", "alt. DOB circa 1960 to 1962"
_OFAC_DATE = re.compile(r"\d{2} [A-Z][a-z]{2} \d{4}|[A-Z][a-z]{2} \d{4}|\d{4}")
_OFAC_NATIONALITY = re.compile(r"(?:nationality|citizen) ([^;.]+)")
UN_NAME_PARTS = ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")
OPENSANCTIONS_PARTY_SCHEMAS = {
    "Person": PartyKind.PERSON,
    "Organization": PartyKind.ENTITY,
    "Company": PartyKind.ENTITY,
    "LegalEntity": PartyKind.ENTITY,
    "PublicBody": PartyKind.ENTITY,
}  # vessels, aircraft and the like are not screened parties


class SanctionsEntry(DomainModel):
    list_name: ScreeningList
    entry_id: str
    kind: PartyKind
    name: str
    aliases: list[str] = []
    birth_dates: list[str] = []  # as published, normalised to YYYY, YYYY-MM or YYYY-MM-DD
    countries: list[str] = []
    programs: list[str] = []
    is_pep: bool = False


def parse_ofac_sdn(sdn_csv: Path, alt_csv: Path) -> Iterator[SanctionsEntry]:
    """OFAC SDN.CSV plus ALT.CSV (aliases): no header row, and "-0-" marks an empty field."""
    aliases: dict[str, list[str]] = defaultdict(list)
    for row in _ofac_rows(alt_csv, columns=5):
        aliases[row[0]].append(row[3])
    for row in _ofac_rows(sdn_csv, columns=12):
        number, name, party_type, programs, remarks = row[0], row[1], row[2], row[3], row[11]
        if party_type not in OFAC_PARTY_TYPES:
            continue
        yield SanctionsEntry(
            list_name=ScreeningList.OFAC_SDN,
            entry_id=number,
            kind=OFAC_PARTY_TYPES[party_type],
            name=name,
            aliases=aliases[number],
            birth_dates=[
                _ofac_date(found)
                for stated in _OFAC_DOB.findall(remarks)
                for found in _OFAC_DATE.findall(stated)  # both ends of "1960 to 1962"
            ],
            countries=[country.strip() for country in _OFAC_NATIONALITY.findall(remarks)],
            programs=[program for program in programs.split("] [") if program != OFAC_EMPTY],
        )


def parse_un_consolidated(xml_path: Path) -> Iterator[SanctionsEntry]:
    root = ET.parse(xml_path).getroot()
    for person in root.iterfind("INDIVIDUALS/INDIVIDUAL"):
        yield SanctionsEntry(
            list_name=ScreeningList.UN_CONSOLIDATED,
            entry_id=_text(person, "REFERENCE_NUMBER") or _text(person, "DATAID"),
            kind=PartyKind.PERSON,
            name=" ".join(part for field in UN_NAME_PARTS if (part := _text(person, field))),
            aliases=_texts(person, "INDIVIDUAL_ALIAS/ALIAS_NAME"),
            birth_dates=[stated for born in person.iterfind("INDIVIDUAL_DATE_OF_BIRTH") for field in ("DATE", "YEAR", "FROM_YEAR", "TO_YEAR") if (stated := _text(born, field))],
            countries=_texts(person, "NATIONALITY/VALUE"),
            programs=_texts(person, "UN_LIST_TYPE"),
        )
    for entity in root.iterfind("ENTITIES/ENTITY"):
        yield SanctionsEntry(
            list_name=ScreeningList.UN_CONSOLIDATED,
            entry_id=_text(entity, "REFERENCE_NUMBER") or _text(entity, "DATAID"),
            kind=PartyKind.ENTITY,
            name=_text(entity, "FIRST_NAME"),
            aliases=_texts(entity, "ENTITY_ALIAS/ALIAS_NAME"),
            countries=_texts(entity, "ENTITY_ADDRESS/COUNTRY"),
            programs=_texts(entity, "UN_LIST_TYPE"),
        )


def parse_opensanctions(csv_path: Path, *, is_pep: bool) -> Iterator[SanctionsEntry]:
    """OpenSanctions `targets.simple.csv`: multi-valued columns are separated by semicolons. `is_pep` is set by the caller: True for the `peps` dataset."""
    csv.field_size_limit(2**31 - 1)  # some rows carry long free-text columns
    with csv_path.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            kind = OPENSANCTIONS_PARTY_SCHEMAS.get(row["schema"])
            if kind is None:
                continue
            yield SanctionsEntry(
                list_name=ScreeningList.OPENSANCTIONS,
                entry_id=row["id"],
                kind=kind,
                name=row["name"],
                aliases=_split(row.get("aliases")),
                birth_dates=_split(row.get("birth_date")),
                countries=_split(row.get("countries")),
                programs=_split(row.get("program_ids")),
                is_pep=is_pep,
            )


def write_entries(entries: Iterable[SanctionsEntry], path: Path) -> int:
    """One JSON line per entry, without default values. Written beside the target and swapped in when complete: a parse that fails halfway never leaves a shortened list behind, which would make screening against it look clean."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    count = 0
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            for entry in entries:
                file.write(entry.model_dump_json(exclude_defaults=True) + "\n")
                count += 1
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return count


def load_entries(paths: Iterable[Path]) -> Iterator[SanctionsEntry]:
    for path in paths:
        with path.open(encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    yield SanctionsEntry.model_validate_json(line)


def synthetic_list(fixtures_dir: Path) -> Path:
    """The demo's committed synthetic list: no download needed."""
    return fixtures_dir / "sanctions" / "synthetic.jsonl"


def _ofac_rows(path: Path, columns: int) -> Iterator[list[str]]:
    with path.open(encoding="utf-8", newline="") as file:
        for row in csv.reader(file):
            if len(row) == columns:  # skips the end-of-file marker row
                yield [field.strip() for field in row]


def _ofac_date(text: str) -> str:
    for pattern, shape in (("%d %b %Y", "%Y-%m-%d"), ("%b %Y", "%Y-%m")):
        try:
            return datetime.strptime(text, pattern).strftime(shape)
        except ValueError:
            continue
    return text  # a bare year


def _text(element: ET.Element, path: str) -> str:
    return (element.findtext(path) or "").strip()


def _texts(element: ET.Element, path: str) -> list[str]:
    return [text.strip() for node in element.iterfind(path) if (text := node.text) and text.strip()]


def _split(value: str | None) -> list[str]:
    return [part for part in (value or "").split(";") if part]
