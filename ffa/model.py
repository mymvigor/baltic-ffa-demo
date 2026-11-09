import calendar
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

VESSELS = {"CAPE": "Capesize", "PMX": "Panamax", "SMX": "Supramax", "HDY": "Handysize"}
DEMO_PRODUCTS = {"CAPE": "DEMO-C5TC", "PMX": "DEMO-P5TC", "SMX": "DEMO-S10TC", "HDY": "DEMO-H7TC"}
UNITS = {"USD/day": ("USD", "day"), "USD/tonne": ("USD", "tonne"), "PTS": ("", "point")}
TENORS = {"month": "月", "quarter": "季", "year": "年", "spot": "现货"}


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
    raise ValueError("日期须为有效的 YYYY-MM-DD 或 Excel 日期单元格")


def price(value):
    raw = str(value).strip()
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", raw):
        raise ValueError("报价须为十进制数，千位逗号须正确分组")
    try:
        number = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        raise ValueError("报价格式无效") from None
    if not number.is_finite() or abs(number) >= 10**12 or number.as_tuple().exponent < -10:
        raise ValueError("报价超出支持的数值范围")
    return number


def period(tenor, start):
    if tenor not in {"month", "quarter", "year"}:
        raise ValueError("交割粒度须为 month、quarter 或 year")
    months = {"month": 1, "quarter": 3, "year": 12}[tenor]
    if start.day != 1 or (start.month - 1) % months:
        raise ValueError("交割起日与期限粒度不匹配")
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
    raise ValueError("合约须为 Jan27、2027-01、Q1 2027 或 Cal 2027 等明确交割期")


def label(tenor, start):
    if start is None:
        return "交割期未确认"
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

