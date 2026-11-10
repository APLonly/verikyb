"""Parser for iXBRL accounts filed at Companies House."""

import io
import re
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation

from verikyb.models import DomainModel

IX = "{http://www.xbrl.org/2013/inlineXBRL}"
XBRLI = "{http://www.xbrl.org/2003/instance}"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
FRC_CORE = re.compile(r"http://xbrl\.frc\.org\.uk/fr/\d{4}-\d{2}-\d{2}/core")
ZERO_FORMATS = ("zerodash", "numdash", "fixed-zero")  # a dash that means 0
COMMA_DECIMAL_FORMATS = ("numdotcomma", "numcommadecimal", "num-comma-decimal", "numspacecomma")


class XbrlFact(DomainModel):
    element: str  # local name in the FRC core taxonomy, e.g. TurnoverRevenue
    value: Decimal
    unit: str  # ISO 4217 code for money, or e.g. "shares"
    period_start: date | None  # None for a balance-sheet (instant) figure
    period_end: date


def parse_ixbrl(document: str) -> list[XbrlFact]:
    raw = document.encode("utf-8")
    # Element names are QNames such as "ns5:Equity"; the prefix varies by filing software, so it is resolved through the document's namespace declarations.
    prefixes = {prefix: uri for _, (prefix, uri) in ET.iterparse(io.BytesIO(raw), events=("start-ns",))}
    root = ET.fromstring(raw)
    periods = _headline_periods(root)
    units = _units(root)

    facts: dict[tuple[str, date | None, date], XbrlFact] = {}
    for tag in root.iter(f"{IX}nonFraction"):
        prefix, _, element = tag.get("name", "").partition(":")
        period = periods.get(tag.get("contextRef", ""))
        value = _number(tag)
        if not FRC_CORE.fullmatch(prefixes.get(prefix, "")) or period is None or value is None:
            continue
        start, end = period
        unit = units.get(tag.get("unitRef", ""), "")
        fact = XbrlFact(element=element, value=value, unit=unit, period_start=start, period_end=end)
        facts.setdefault((element, start, end), fact)
    return list(facts.values())


def _headline_periods(root: ET.Element) -> dict[str, tuple[date | None, date]]:
    periods: dict[str, tuple[date | None, date]] = {}
    for context in root.iter(f"{XBRLI}context"):
        dimensional = context.find(f".//{XBRLI}segment") is not None
        period = context.find(f"{XBRLI}period")
        if dimensional or period is None:
            continue
        instant = period.findtext(f"{XBRLI}instant")
        start, end = period.findtext(f"{XBRLI}startDate"), period.findtext(f"{XBRLI}endDate")
        if instant:
            periods[context.get("id", "")] = (None, date.fromisoformat(instant.strip()))
        elif start and end:
            periods[context.get("id", "")] = (
                date.fromisoformat(start.strip()),
                date.fromisoformat(end.strip()),
            )
    return periods


def _units(root: ET.Element) -> dict[str, str]:
    """Unit ID to its measure without the prefix: `iso4217:GBP` becomes GBP."""
    return {unit.get("id", ""): measure.rpartition(":")[2] for unit in root.iter(f"{XBRLI}unit") if (measure := unit.findtext(f"{XBRLI}measure"))}


def _number(tag: ET.Element) -> Decimal | None:
    if tag.get(XSI_NIL) == "true":
        return None
    number_format = tag.get("format", "")
    text = "".join(tag.itertext()).strip()
    if number_format.endswith(ZERO_FORMATS):
        value = Decimal(0)
    else:
        if number_format.endswith(COMMA_DECIMAL_FORMATS):
            text = text.replace(".", "").replace(" ", "").replace(",", ".")
        digits = re.sub(r"[^0-9.]", "", text)
        try:
            value = Decimal(digits)
        except InvalidOperation:  # empty or not one number ("1.2.3"): no figure, not a crash
            return None
    value = value.scaleb(int(tag.get("scale", "0")))
    return -value if tag.get("sign") == "-" else value
