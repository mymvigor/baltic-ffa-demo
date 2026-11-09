import math
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal

from .model import DEMO_PRODUCTS, Quote, period


def data(mode="normal"):
    fixed, rolling, statuses = [], [], []
    days = [date(2026, 9, 21) + timedelta(days=i) for i in range(47)]
    days = [day for day in days if day.weekday() < 5]
    if mode == "latest":
        days = days[-1:]
    for index, (vessel, product) in enumerate(DEMO_PRODUCTS.items()):
        failed = mode == "failed" and vessel == "CAPE"
        statuses.append(dict(vessel=vessel, status="failed" if failed else "ok",
                             attempted_at=None, succeeded_at=None, latest_day=days[-1],
                             inserted=0, revised=0, repeated=0, failed=int(failed)))
        for n, quote_day in enumerate(days):
            base = 22000 - index * 2600 + 500 * math.sin(n / 3) + n * 20
            season = 10 if quote_day < date(2026, 10, 26) else 11
            terms = [("spot", None, None)]
            terms.extend(("month", date(2026, month, 1), code)
                         for month, code in [(season, "CURMON"), (season + 1, "+1MON")])
            terms.extend([("month", date(2027, 1, 1), "+2MON" if season == 11 else "+3MON"),
                          ("quarter", date(2027, 1, 1), "+1Q"),
                          ("quarter", date(2027, 4, 1), "+2Q"),
                          ("year", date(2027, 1, 1), "+1CAL"),
                          ("year", date(2028, 1, 1), "+2CAL")])
            # A synthetic vendor switches before month-end on purpose.
            for j, (tenor, start, code) in enumerate(terms):
                if n == 19 and j == 2:
                    continue
                end = period(tenor, start)[1] if start else None
                value = round(base + (start.month * 200 + (start.year - 2026) * 400 if start else -700))
                quote = Quote(quote_day, vessel, product, "BFA" if start else "Spot", "USD", "day",
                              tenor, start, end, code, code, "合成示例", "api", Decimal(value))
                row = dict(asdict(quote), identity=quote.identity, source="合成")
                fixed.append(row)
                if code:
                    rolling.append(row)
    return fixed, rolling, statuses

