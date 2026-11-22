"""Download the public sanctions and PEP lists and turn them into one entry shape (`make data`)."""

import os
import sys
import tempfile
from pathlib import Path

import httpx

from verikyb.sources.sanctions import (
    parse_ofac_sdn,
    parse_opensanctions,
    parse_un_consolidated,
    write_entries,
)

ROOT = Path(__file__).resolve().parents[2]
RAW, PROCESSED = ROOT / "data" / "raw", ROOT / "data" / "processed" / "sanctions"
OFAC = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports"
OPENSANCTIONS = "https://data.opensanctions.org/datasets/latest"
DOWNLOADS = {
    "ofac/SDN.CSV": f"{OFAC}/SDN.CSV",
    "ofac/ALT.CSV": f"{OFAC}/ALT.CSV",
    "un/consolidated.xml": "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
    "opensanctions/sanctions.csv": f"{OPENSANCTIONS}/sanctions/targets.simple.csv",
    "opensanctions/peps.csv": f"{OPENSANCTIONS}/peps/targets.simple.csv",
}


def download(client: httpx.Client, url: str, target: Path) -> None:
    """Streamed to a temporary file and moved into place only when complete."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, suffix=".part")
    try:
        with os.fdopen(handle, "wb") as file, client.stream("GET", url) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                file.write(chunk)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main() -> int:
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, read=300)) as client:
        for relative, url in DOWNLOADS.items():
            print(f"downloading {relative}")
            download(client, url, RAW / relative)
    counts = {
        "ofac_sdn": write_entries(parse_ofac_sdn(RAW / "ofac/SDN.CSV", RAW / "ofac/ALT.CSV"), PROCESSED / "ofac_sdn.jsonl"),
        "un_consolidated": write_entries(parse_un_consolidated(RAW / "un/consolidated.xml"), PROCESSED / "un_consolidated.jsonl"),
        "opensanctions_sanctions": write_entries(
            parse_opensanctions(RAW / "opensanctions/sanctions.csv", is_pep=False),
            PROCESSED / "opensanctions_sanctions.jsonl",
        ),
        "opensanctions_peps": write_entries(
            parse_opensanctions(RAW / "opensanctions/peps.csv", is_pep=True),
            PROCESSED / "opensanctions_peps.jsonl",
        ),
    }
    for name, count in counts.items():
        print(f"{name}: {count} entries")
    return 0 if all(counts.values()) else 1  # an empty list means a format change: look first


if __name__ == "__main__":
    sys.exit(main())
