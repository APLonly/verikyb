"""Synthetic Indian KYB documents, each rendered twice from one layout: a PDF with a text layer and a PNG that looks scanned (rotated, blurred, noisy) for the OCR and vision path.

Every page carries the watermark; the fields each document shows are returned as ground truth.
"""

import hashlib
import math
import random
import textwrap
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from spec import DocumentType, IndiaCompany, SpecAddress

WATERMARK = "SYNTHETIC TEST DOCUMENT - NOT VALID"
PAGE_WIDTH, PAGE_HEIGHT = A4  # points
PNG_SCALE = 150 / 72  # 150 dpi


@dataclass(frozen=True)
class Text:
    x: float  # points from the left
    y: float  # points from the top, at the baseline
    text: str
    size: float = 10
    bold: bool = False
    centred: bool = False


def render(company: IndiaCompany, doc_type: DocumentType, out_dir: Path) -> dict[str, Any]:
    """Writes `<cin>_<type>.pdf` and `<cin>_<type>_scan.png`; returns the ground truth."""
    lines, fields = LAYOUTS[doc_type](company)
    stem = out_dir / f"{company.cin}_{doc_type}"
    _pdf(lines, stem.with_suffix(".pdf"), title=f"{company.name}: {doc_type}")
    _scanned_png(lines, stem.parent / f"{stem.name}_scan.png")
    return {
        "company": company.cin,
        "doc_type": doc_type,
        "pdf": f"{stem.name}.pdf",
        "scan": f"{stem.name}_scan.png",
        "fields": fields,
    }


def _page(
    issuer: list[str],
    title: str,
    rows: list[tuple[str, str]],
    paragraph: str = "",
    table: list[list[str]] | None = None,
    footer: list[str] | None = None,
) -> list[Text]:
    lines = [Text(PAGE_WIDTH / 2, 28, WATERMARK, size=9, bold=True, centred=True)]
    y = 70.0
    for issuer_line in issuer:
        lines.append(Text(PAGE_WIDTH / 2, y, issuer_line, size=11, bold=True, centred=True))
        y += 16
    lines.append(Text(PAGE_WIDTH / 2, y + 18, title, size=15, bold=True, centred=True))
    y += 60
    for paragraph_line in textwrap.wrap(paragraph, 88):
        lines.append(Text(60, y, paragraph_line))
        y += 15
    y += 10 if paragraph else 0
    for label, value in rows:
        lines.append(Text(60, y, f"{label}:", bold=True))
        for value_line in textwrap.wrap(value, 52):
            lines.append(Text(250, y, value_line))
            y += 15
        y += 8
    for row_number, row in enumerate(table or []):
        for column, cell in enumerate(row):
            lines.append(Text((60, 330, 450)[column], y, cell, bold=row_number == 0))
        y += 18
    for footer_line in footer or []:
        y += 14
        lines.append(Text(60, y, footer_line, size=9))
    lines.append(Text(PAGE_WIDTH / 2, PAGE_HEIGHT - 30, WATERMARK, size=9, bold=True, centred=True))
    return lines


def _incorporation(c: IndiaCompany) -> tuple[list[Text], dict[str, Any]]:
    address = _address(c.document_address or c.address, c.state)
    fields = {
        "company_name": c.name,
        "cin": c.cin,
        "pan": c.pan,
        "roc": c.roc,
        "incorporation_date": c.incorporated_on.isoformat(),
        "registered_office": address,
    }
    lines = _page(
        [
            "GOVERNMENT OF INDIA",
            "MINISTRY OF CORPORATE AFFAIRS",
            f"Office of the Registrar of Companies, {c.roc}",
        ],
        "CERTIFICATE OF INCORPORATION",
        [
            ("Corporate Identity Number (CIN)", c.cin),
            ("Permanent Account Number (PAN)", c.pan),
            ("Registered office", address),
        ],
        paragraph=f"I hereby certify that {c.name.upper()} is incorporated on this {_printed(c.incorporated_on)} under the Companies Act, 2013 (18 of 2013) and that the company is limited by shares.",
        footer=[
            f"Given under my hand at {c.roc.removeprefix('RoC-')} on {_printed(c.incorporated_on)}.",
            "Registrar of Companies (synthetic signature)",
        ],
    )
    return lines, fields


def _gst(c: IndiaCompany) -> tuple[list[Text], dict[str, Any]]:
    address = _address(c.document_address or c.address, c.state)
    liability = max(c.incorporated_on, date(2017, 7, 1))  # GST began on 1 July 2017
    fields = {
        "gstin": c.gstin,
        "legal_name": c.name,
        "trade_name": c.name,
        "constitution": "Private Limited Company",
        "address": address,
        "liability_date": liability.isoformat(),
        "registration_type": "Regular",
    }
    lines = _page(
        ["Government of India", "Form GST REG-06", "[See Rule 10(1)]"],
        "Registration Certificate",
        [
            ("Registration Number", c.gstin or ""),
            ("Legal Name", c.name),
            ("Trade Name, if any", c.name),
            ("Constitution of Business", "Private Limited Company"),
            ("Address of Principal Place of Business", address),
            ("Date of Liability", _printed(liability)),
            ("Period of Validity", f"From {_printed(liability)} To Not Applicable"),
            ("Type of Registration", "Regular"),
        ],
        footer=["This is a system generated digitally signed Registration Certificate (synthetic)."],
    )
    return lines, fields


def _pan(c: IndiaCompany) -> tuple[list[Text], dict[str, Any]]:
    fields = {"pan": c.pan, "name": c.name, "incorporation_date": c.incorporated_on.isoformat()}
    lines = _page(
        ["INCOME TAX DEPARTMENT", "GOVT. OF INDIA"],
        "Permanent Account Number Card",
        [
            ("Permanent Account Number", c.pan),
            ("Name", c.name.upper()),
            ("Date of Incorporation/Formation", _printed(c.incorporated_on)),
        ],
    )
    return lines, fields


def _shareholding(c: IndiaCompany) -> tuple[list[Text], dict[str, Any]]:
    holders = [{"name": s.document_name or s.name, "shares": s.shares, "pct": s.pct} for s in c.shareholders]
    fields = {
        "company_name": c.name,
        "cin": c.cin,
        "as_at": c.shareholding.as_at.isoformat(),
        "valid_until": c.shareholding.valid_until.isoformat(),
        "shareholders": holders,
    }
    table = [["Name of shareholder", "Equity shares", "% holding"]] + [[h["name"], f"{h['shares']:,}", f"{h['pct']:g}%"] for h in holders]
    lines = _page(
        [c.name.upper(), f"CIN: {c.cin}", _address(c.address, c.state)],
        f"LIST OF SHAREHOLDERS AS AT {_printed(c.shareholding.as_at)}",
        [
            (
                "Total issued capital",
                f"{sum(s.shares for s in c.shareholders):,} equity shares of INR 10 each",
            ),
            ("This extract is valid until", _printed(c.shareholding.valid_until)),
        ],
        table=table,
        footer=["Certified true copy", "Company Secretary (synthetic signature)"],
    )
    return lines, fields


LAYOUTS = {
    "certificate_of_incorporation": _incorporation,
    "gst_certificate": _gst,
    "pan_card": _pan,
    "shareholding_extract": _shareholding,
}


def _pdf(lines: list[Text], path: Path, title: str) -> None:
    canvas = Canvas(str(path), pagesize=A4, invariant=1)  # invariant: byte-identical re-runs
    canvas.setTitle(title)
    canvas.setCreator("VeriKYB synthetic data generator")
    for line in lines:
        canvas.setFont("Helvetica-Bold" if line.bold else "Helvetica", line.size)
        draw = canvas.drawCentredString if line.centred else canvas.drawString
        draw(line.x, PAGE_HEIGHT - line.y, line.text)
    canvas.saveState()
    canvas.setFillColorRGB(0.8, 0.1, 0.1)
    canvas.setFillAlpha(0.18)
    canvas.translate(PAGE_WIDTH / 2, PAGE_HEIGHT / 2)
    canvas.rotate(35)
    canvas.setFont("Helvetica-Bold", 34)
    canvas.drawCentredString(0, 0, WATERMARK)
    canvas.restoreState()
    canvas.showPage()
    canvas.save()


def _scanned_png(lines: list[Text], path: Path) -> None:
    rng = random.Random(int(hashlib.sha256(path.name.encode()).hexdigest()[:8], 16))
    size = (round(PAGE_WIDTH * PNG_SCALE), round(PAGE_HEIGHT * PNG_SCALE))
    page = Image.new("L", size, 255)
    draw = ImageDraw.Draw(page)
    for line in lines:
        font = ImageFont.load_default(size=line.size * PNG_SCALE)
        draw.text(
            (line.x * PNG_SCALE, line.y * PNG_SCALE),
            line.text,
            fill=0,
            font=font,
            anchor="ms" if line.centred else "ls",
            stroke_width=1 if line.bold else 0,
            stroke_fill=0,
        )
    page = Image.composite(Image.new("L", size, 150), page, _watermark_mask(size))
    page = page.rotate(rng.uniform(-2.5, 2.5), resample=Image.Resampling.BICUBIC, fillcolor=255)
    page = page.filter(ImageFilter.GaussianBlur(rng.uniform(0.6, 1.2)))
    page = Image.blend(page, _noise(size, rng), alpha=0.12)
    page.save(path, optimize=True)


def _watermark_mask(size: tuple[int, int]) -> Image.Image:
    """Drawn on a square as wide as the page diagonal, so rotating it clips nothing."""
    side = math.ceil(math.hypot(*size))
    mask = Image.new("L", (side, side), 0)
    font = ImageFont.load_default(size=34 * PNG_SCALE)
    ImageDraw.Draw(mask).text((side / 2, side / 2), WATERMARK, fill=90, font=font, anchor="mm")
    mask = mask.rotate(35, resample=Image.Resampling.BICUBIC)
    left, top = (side - size[0]) // 2, (side - size[1]) // 2
    return mask.crop((left, top, left + size[0], top + size[1]))


def _noise(size: tuple[int, int], rng: random.Random) -> Image.Image:
    """Seeded noise tiled across the page; Pillow's own noise effect cannot be seeded."""
    tile_size = 256
    tile = Image.frombytes(
        "L",
        (tile_size, tile_size),
        bytes(min(255, max(0, round(rng.gauss(128, 40)))) for _ in range(tile_size * tile_size)),
    )
    noise = Image.new("L", size)
    for x in range(0, size[0], tile_size):
        for y in range(0, size[1], tile_size):
            noise.paste(tile, (x, y))
    return noise


def _address(address: SpecAddress, state: str) -> str:
    return ", ".join([*address.lines, address.locality, f"{state} {address.postal_code}", "India"])


def _printed(day: date) -> str:
    return day.strftime("%d/%m/%Y")
