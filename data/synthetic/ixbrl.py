"""Synthetic iXBRL accounts with the quirks of real Companies House filings, so the parser meets them: an arbitrary namespace prefix, a figure tagged twice, a dimensional breakdown of equity, a dash for zero, and losses tagged with sign="-" and shown in brackets."""

from datetime import date, timedelta
from html import escape

from spec import Accounts, AccountsYear, UkCompany

CORE = "ns5"  # filing software picks the prefix; the parser must resolve it from the namespace
NAMESPACES = {
    "": "http://www.w3.org/1999/xhtml",
    "ix": "http://www.xbrl.org/2013/inlineXBRL",
    "ixt2": "http://www.xbrl.org/inlineXBRL/transformation/2011-07-31",
    "xbrli": "http://www.xbrl.org/2003/instance",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
    "link": "http://www.xbrl.org/2003/linkbase",
    "xlink": "http://www.w3.org/1999/xlink",
    "iso4217": "http://www.xbrl.org/2003/iso4217",
    CORE: "http://xbrl.frc.org.uk/fr/2025-01-01/core",
}
WATERMARK = "SYNTHETIC TEST DOCUMENT - NOT VALID"
SHARE_CAPITAL = 100


def accounts_ixbrl(company: UkCompany, accounts: Accounts) -> str:
    contexts = "".join(_contexts(company.number, year) for year in accounts.years)
    columns = "".join(f"<th>{year.period_end:%d %B %Y}</th>" for year in accounts.years)
    rows = [
        ("Fixed assets", "FixedAssets", "I", lambda y: y.fixed_assets),
        ("Current assets", "CurrentAssets", "I", lambda y: y.current_assets),
        ("Cash at bank and in hand", "CashBankOnHand", "I", lambda y: y.cash),
        ("Creditors: amounts falling due within one year", "Creditors", "I", lambda y: y.creditors),
        (
            "Provisions for liabilities",
            "ProvisionsForLiabilitiesBalanceSheetSubtotal",
            "I",
            lambda y: 0,
        ),
        ("Net assets", "NetAssetsLiabilities", "I", lambda y: y.net_assets),
        ("Total equity", "Equity", "I", lambda y: y.net_assets),
        ("Called up share capital", "Equity", "Ishare", lambda y: SHARE_CAPITAL),
        ("Retained earnings", "Equity", "Iretained", lambda y: y.net_assets - SHARE_CAPITAL),
        ("Shareholders' funds (note)", "Equity", "I", lambda y: y.net_assets),
    ]
    if not accounts.balance_sheet_only:
        rows[:0] = [
            ("Turnover", "TurnoverRevenue", "D", lambda y: y.turnover),
            ("Operating profit/(loss)", "OperatingProfitLoss", "D", lambda y: y.operating_profit),
            ("Profit/(loss) for the financial year", "ProfitLoss", "D", lambda y: y.profit_loss),
        ]
    body = "".join(f"<tr><td>{escape(label)}</td>" + "".join(f"<td>{_fact(element, f'{kind}{y.period_end}', value(y))}</td>" for y in accounts.years) + "</tr>" for label, element, kind, value in rows)
    declarations = " ".join(f'xmlns{":" + prefix if prefix else ""}="{uri}"' for prefix, uri in NAMESPACES.items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<html {declarations}><head><title>{escape(company.name)} accounts</title></head><body>"
        '<div style="display:none"><ix:header><ix:references>'
        '<link:schemaRef xlink:type="simple" '
        'xlink:href="https://xbrl.frc.org.uk/FRS-102/2025-01-01/FRS-102-2025-01-01.xsd"/>'
        f"</ix:references><ix:resources>{contexts}"
        '<xbrli:unit id="GBP"><xbrli:measure>iso4217:GBP</xbrli:measure></xbrli:unit>'
        "</ix:resources></ix:header></div>"
        f"<p><strong>{WATERMARK}</strong></p>"
        f"<h1>{escape(company.name)}</h1><p>Company number {company.number}</p>"
        f"<table><tr><th></th>{columns}</tr>{body}</table>"
        f"<p><strong>{WATERMARK}</strong></p></body></html>\n"
    )


def _contexts(number: str, year: AccountsYear) -> str:
    end = year.period_end
    start = date(end.year - 1, end.month, end.day) + timedelta(days=1)
    instant = f"<xbrli:instant>{end}</xbrli:instant>"
    return (
        _context(
            f"D{end}",
            number,
            f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>",
        )
        + _context(f"I{end}", number, instant)
        + _context(f"Ishare{end}", number, instant, "CalledUpShareCapital")
        + _context(f"Iretained{end}", number, instant, "RetainedEarningsAccumulatedLosses")
    )


def _context(context_id: str, number: str, period: str, equity_component: str = "") -> str:
    segment = f'<xbrli:segment><xbrldi:explicitMember dimension="{CORE}:ComponentsEquityDimension">{CORE}:{equity_component}</xbrldi:explicitMember></xbrli:segment>' if equity_component else ""
    return f'<xbrli:context id="{context_id}"><xbrli:entity><xbrli:identifier scheme="http://www.companieshouse.gov.uk/">{number}</xbrli:identifier>{segment}</xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>'


def _fact(element: str, context: str, value: int | None) -> str:
    if value is None:
        return ""
    attributes = f'name="{CORE}:{element}" contextRef="{context}" unitRef="GBP" decimals="0"'
    if value == 0:
        return f'<ix:nonFraction {attributes} format="ixt2:zerodash">-</ix:nonFraction>'
    sign = ' sign="-"' if value < 0 else ""
    tag = f'<ix:nonFraction {attributes} scale="0" format="ixt2:numdotdecimal"{sign}>{abs(value):,}</ix:nonFraction>'
    return f"({tag})" if value < 0 else tag
