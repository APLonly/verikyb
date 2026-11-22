import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from verikyb.models import PartyKind, ScreeningList
from verikyb.sources.sanctions import (
    SanctionsEntry,
    load_entries,
    parse_ofac_sdn,
    parse_opensanctions,
    parse_un_consolidated,
    synthetic_list,
    write_entries,
)

ROOT = Path(__file__).resolve().parents[2]

# Samples in each list's published format (checked against the real files): OFAC has no header, "-0-" for empty, programs joined by "] [", dates of birth and nationality inside the remarks and an end-of-file marker.
SAMPLES = ROOT / "tests" / "data" / "sanctions"


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


# OFAC


@pytest.fixture
def ofac() -> dict[str, SanctionsEntry]:
    entries = parse_ofac_sdn(SAMPLES / "SDN.CSV", SAMPLES / "ALT.CSV")
    return {entry.entry_id: entry for entry in entries}


def test_ofac_keeps_individuals_and_entities_not_vessels_or_aircraft(
    ofac: dict[str, SanctionsEntry],
) -> None:
    assert set(ofac) == {"36", "306", "2674", "7001"}
    assert ofac["36"].kind is PartyKind.ENTITY
    assert ofac["2674"].kind is PartyKind.PERSON
    assert all(entry.list_name is ScreeningList.OFAC_SDN for entry in ofac.values())


def test_ofac_reads_aliases_and_programs(ofac: dict[str, SanctionsEntry]) -> None:
    assert ofac["36"].aliases == ["AERO-CARIBBEAN"]
    assert ofac["2674"].aliases == ["FADLALLAH, Muhammad Husayn", "FADLALLAH, Sayyid"]
    assert ofac["306"].aliases == []
    assert ofac["306"].programs == ["CUBA", "CUBA-EO14404"]


def test_ofac_reads_dates_of_birth_and_nationality_from_the_remarks(
    ofac: dict[str, SanctionsEntry],
) -> None:
    assert ofac["2674"].birth_dates == ["1938", "1936"]
    assert ofac["2674"].countries == ["Lebanon"]
    # Both ends of a range, a full date and a month, each normalised.
    assert ofac["7001"].birth_dates == ["1960", "1962", "1948-12-10", "1955-03"]
    assert ofac["7001"].countries == ["Iran"]
    assert ofac["36"].birth_dates == []  # an entity: "-0-" remarks


# UN


@pytest.fixture
def un() -> dict[str, SanctionsEntry]:
    return {e.entry_id: e for e in parse_un_consolidated(SAMPLES / "consolidated.xml")}


def test_un_reads_individuals(un: dict[str, SanctionsEntry]) -> None:
    eric = un["CDi.001"]
    assert (eric.kind, eric.name, eric.programs) == (PartyKind.PERSON, "ERIC BADEGE", ["DRC"])
    assert eric.aliases == ["Eric Badege K."]  # the blank alias is dropped
    assert eric.birth_dates == ["1971", "1965", "1967"]
    assert eric.countries == ["Democratic Republic of the Congo"]


def test_un_falls_back_to_the_data_id_and_reads_full_dates(un: dict[str, SanctionsEntry]) -> None:
    assert un["6908000"].birth_dates == ["1958-01-01"]


def test_un_reads_entities(un: dict[str, SanctionsEntry]) -> None:
    adf = un["CDe.001"]
    assert (adf.kind, adf.name, adf.aliases) == (
        PartyKind.ENTITY,
        "ADF",
        ["Allied Democratic Forces"],
    )
    assert adf.countries == ["Democratic Republic of the Congo"]


# OpenSanctions


def test_opensanctions_splits_multi_valued_columns(tmp_path: Path) -> None:
    entries = list(parse_opensanctions(SAMPLES / "targets.simple.csv", is_pep=False))

    assert [e.entry_id for e in entries] == ["NK-1", "Q1"]  # the vessel is left out
    company, person = entries
    assert company.kind is PartyKind.ENTITY
    assert company.aliases == ["Yatai New City", "Shwe Kokko"]
    assert (company.countries, company.programs) == (["mm"], ["US-GLOMAG"])
    assert person.birth_dates == ["1970-05-01", "1970"]
    assert person.countries == ["gb", "fr"]
    assert person.programs == []
    assert not any(e.is_pep for e in entries)


def test_opensanctions_peps_are_marked(tmp_path: Path) -> None:
    entries = parse_opensanctions(SAMPLES / "targets.simple.csv", is_pep=True)

    assert all(entry.is_pep for entry in entries)


def test_opensanctions_without_a_program_column_still_parses(tmp_path: Path) -> None:
    older = "id,schema,name,aliases,birth_date,countries\nQ2,Person,Old Format,,,de\n"

    [entry] = parse_opensanctions(write(tmp_path, "old.csv", older), is_pep=False)

    assert (entry.name, entry.countries, entry.programs) == ("Old Format", ["de"], [])


# One shape: writing and loading


def test_every_list_writes_and_loads_in_the_same_shape(tmp_path: Path, ofac: dict[str, SanctionsEntry], un: dict[str, SanctionsEntry]) -> None:
    entries = [*ofac.values(), *un.values()]

    count = write_entries(entries, tmp_path / "out" / "all.jsonl")

    assert count == len(entries)
    assert list(load_entries([tmp_path / "out" / "all.jsonl"])) == entries


def test_written_lists_leave_out_defaults_and_use_lf(tmp_path: Path) -> None:
    entry = SanctionsEntry(list_name=ScreeningList.OFAC_SDN, entry_id="1", kind=PartyKind.ENTITY, name="X")
    write_entries([entry], tmp_path / "x.jsonl")

    raw = (tmp_path / "x.jsonl").read_bytes()
    assert raw == b'{"list_name":"ofac_sdn","entry_id":"1","kind":"entity","name":"X"}\n'


def test_a_parse_that_fails_halfway_never_leaves_a_shortened_list(tmp_path: Path) -> None:
    target = tmp_path / "ofac.jsonl"
    good = SanctionsEntry(list_name=ScreeningList.OFAC_SDN, entry_id="1", kind=PartyKind.ENTITY, name="Old")
    write_entries([good], target)

    def failing() -> Iterator[SanctionsEntry]:
        yield good.model_copy(update={"name": "New"})
        raise ValueError("the file changed format")

    with pytest.raises(ValueError, match="changed format"):
        write_entries(failing(), target)

    assert [entry.name for entry in load_entries([target])] == ["Old"]  # the old list stands
    assert [path.name for path in tmp_path.iterdir()] == ["ofac.jsonl"]  # no temporary left


# The synthetic list


def test_the_synthetic_list_loads_without_a_download() -> None:
    entries = list(load_entries([synthetic_list(ROOT / "data" / "fixtures")]))

    assert entries
    assert all(entry.list_name is ScreeningList.SYNTHETIC for entry in entries)
    assert {entry.kind for entry in entries} == {PartyKind.PERSON, PartyKind.ENTITY}
    assert len({entry.entry_id for entry in entries}) == len(entries)


# The licences


def test_each_list_has_its_licence_documented() -> None:
    doc = (ROOT / "docs" / "data-sources.md").read_text(encoding="utf-8")
    rows = {line.split("|")[1] for line in doc.splitlines() if line.startswith("| [")}

    for source in ("OFAC SDN list", "UN Security Council Consolidated List", "OpenSanctions"):
        assert any(source in row for row in rows), f"{source} has no row in docs/data-sources.md"
    assert "CC BY-NC 4.0" in doc
    assert "public domain" in doc


# The download script


def download_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("download", ROOT / "data" / "scripts" / "download.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_download_is_moved_into_place_only_when_complete(tmp_path: Path) -> None:
    script = download_script()
    target = tmp_path / "ofac" / "SDN.CSV"
    ok = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text="rows")))
    script.download(ok, "https://lists.example.test/SDN.CSV", target)
    assert target.read_text() == "rows"

    failing = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        script.download(failing, "https://lists.example.test/SDN.CSV", target)

    assert target.read_text() == "rows"  # the last good download stands
    assert [path.name for path in target.parent.iterdir()] == ["SDN.CSV"]


def test_make_data_covers_every_list() -> None:
    assert set(download_script().DOWNLOADS) == {
        "ofac/SDN.CSV",
        "ofac/ALT.CSV",
        "un/consolidated.xml",
        "opensanctions/sanctions.csv",
        "opensanctions/peps.csv",
    }
