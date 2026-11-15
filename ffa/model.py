import calendar
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

VESSELS = {"CAPE": "Capesize", "PMX": "Panamax", "SMX": "Supramax", "HDY": "Handysize"}
DEMO_PRODUCTS = {"CAPE": "DEMO-C5TC", "PMX": "DEMO-P5TC", "SMX": "DEMO-S10TC", "HDY": "DEMO-H7TC"}
UNITS = {"USD/day": ("USD", "day"), "USD/tonne": ("USD", "tonne"), "PTS": ("", "point")}
TENORS = {"month": "Monthly", "quarter": "Quarterly", "year": "Annual", "spot": "Spot"}


def day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ValueError("Use a valid YYYY-MM-DD date or an Excel date cell")


def price(value):
    raw = str(value).strip()
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", raw):
        raise ValueError("Price must be decimal, with correctly grouped thousands separators")
    try:
        number = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        raise ValueError("Invalid price format") from None
    if not number.is_finite() or abs(number) >= 10**12 or number.as_tuple().exponent < -10:
        raise ValueError("Price is outside the supported numeric range")
    return number


def period(tenor, start):
    if tenor not in {"month", "quarter", "year"}:
        raise ValueError("Delivery tenor must be month, quarter or year")
    months = {"month": 1, "quarter": 3, "year": 12}[tenor]
    if start.day != 1 or (start.month - 1) % months:
        raise ValueError("Delivery start does not match the selected tenor")
    month = start.month + months - 1
    return start, date(start.year, month, calendar.monthrange(start.year, month)[1])


def contract(value):
    raw = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        return "month", *period("month", day(raw + "-01"))
    match = re.fullmatch(r"([A-Za-z]{3})\s*(\d{2}|\d{4})", raw)
    months = {label.lower(): index for index, label in enumerate(calendar.month_abbr) if index}
    if match and match[1].lower() in months:
        year = int(match[2]) + (2000 if len(match[2]) == 2 else 0)
        return "month", *period("month", date(year, months[match[1].lower()], 1))
    match = re.fullmatch(r"Q([1-4])\s*[- ]?\s*(\d{4})", raw, re.I)
    if match:
        return "quarter", *period("quarter", date(int(match[2]), 3 * int(match[1]) - 2, 1))
    match = re.fullmatch(r"(?:CAL\s*)?(\d{4})", raw, re.I)
    if match:
        return "year", *period("year", date(int(match[1]), 1, 1))
    raise ValueError("Specify a delivery period such as Jan27, 2027-01, Q1 2027 or Cal 2027")


def label(tenor, start):
    if start is None:
        return "Unconfirmed delivery"
    if tenor == "quarter":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if tenor == "year":
        return f"Cal {start.year}"
    return start.strftime("%Y-%m")


@dataclass(frozen=True)
class Quote:
    quote_day: date
    vessel: str
    product: str
    kind: str
    currency: str
    unit: str
    tenor: str
    delivery_start: date | None
    delivery_end: date | None
    series: str | None
    rolling: str | None
    mapping: str
    source: str
    price: Decimal

    @property
    def identity(self):
        keys = [self.vessel, self.product, self.kind, self.currency, self.unit, self.tenor]
        if self.delivery_start is not None:
            keys.extend([str(self.delivery_start), str(self.delivery_end)])
        elif self.kind == "BFA":
            keys.extend(["unresolved", self.series, self.rolling])
        return json.dumps(keys, ensure_ascii=False, separators=(",", ":"))

    @property
    def key(self):
        return self.identity, self.quote_day

