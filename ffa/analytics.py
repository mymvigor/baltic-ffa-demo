import pandas as pd

from .model import label

FIELDS = ["identity", "quote_day", "vessel", "product", "kind", "currency", "unit", "tenor",
          "delivery_start", "delivery_end", "series", "rolling", "mapping", "source", "price"]


def frame(rows):
    result = pd.DataFrame(rows, columns=FIELDS)
    result["price"] = result["price"].astype(float)
    for field in ("quote_day", "delivery_start", "delivery_end"):
        result[field] = pd.to_datetime(result[field])
    return result


def unit_label(currency, unit):
    return f"{currency}/{unit}" if currency else "PTS"


def delivery_label(tenor, start):
    if tenor == "spot":
        return "Spot"
    if pd.isna(start):
        return "Unconfirmed delivery"
    if tenor == "month":
        return start.strftime("%b %y")
    return label(tenor, start)


def options(fixed, rolling):
    eligible = fixed[(fixed.kind == "Spot") | fixed.delivery_start.notna()]
    series = {}
    ordered = eligible.sort_values(["kind", "tenor", "delivery_start"])
    for _, rows in ordered.groupby("identity", sort=False):
        row = rows.iloc[0]
        name = delivery_label(row.tenor, row.delivery_start)
        key = (row.vessel, row["product"], row.kind, row.currency, row.unit, row.tenor, "Fixed", row.identity)
        series[key] = (name, rows)
    fields = ["vessel", "product", "kind", "currency", "unit", "tenor", "rolling"]
    for components, data in rolling.groupby(fields, sort=True):
        *base, code = components
        unresolved = data.delivery_start.isna().any()
        multiple = data.duplicated("quote_day", keep=False)
        if multiple.any():
            # Multiple supplier series for a rolling code must remain independent.
            for index, (_, sub) in enumerate(data.groupby("series"), 1):
                if not sub.duplicated("quote_day").any():
                    series[(*base, "Rolling", f"{code}#{index}")] = (f"{code} · Series {index}", sub)
        else:
            name = f"{code} · Unconfirmed delivery" if unresolved else code
            series[(*base, "Rolling", code)] = (name, data)
    return series


def timeline(rows, days):
    out = rows.set_index("quote_day").sort_index().reindex(days)
    known = (out.tenor.fillna("") + "|" + out.delivery_start.dt.strftime("%Y-%m-%d").fillna("")
             + "|" + out.delivery_end.dt.strftime("%Y-%m-%d").fillna(""))
    resolved = out.delivery_start.notna() & out.delivery_end.notna()
    spot = out.kind.eq("Spot")
    observed = out.price.notna()
    # An unresolved quote has no demonstrably continuous delivery segment.
    tokens = known.where(resolved, pd.Series(days.strftime("unknown-%Y-%m-%d"), index=days))
    tokens = tokens.mask(spot, "spot").where(observed).ffill()
    out["segment"] = tokens.ne(tokens.shift()).cumsum()
    # Retain boundary metadata before display windows are clipped.
    active = out.loc[observed]
    confirmed = active.delivery_start.notna() & active.delivery_end.notna() & active.kind.ne("Spot")
    rolls = active.segment.ne(active.segment.shift()) & confirmed & confirmed.shift(fill_value=False)
    contracts = active.delivery_start.dt.strftime("%Y-%m-%d") + " → " + active.delivery_end.dt.strftime("%Y-%m-%d")
    out["roll_from"] = contracts.shift().where(rolls)
    out["roll_to"] = contracts.where(rolls)
    return out


def indicators(data):
    out = data.copy()
    observed = out.dropna(subset=["price"])
    grouped = observed.groupby("segment").price
    out["ma5"] = grouped.rolling(5, min_periods=5).mean().droplevel(0)
    window = grouped.rolling(20, min_periods=20)
    mean, deviation = window.mean().droplevel(0), window.std(ddof=0).droplevel(0)
    out["ma20"] = mean
    out["upper"] = mean + 2 * deviation
    out["lower"] = mean - 2 * deviation
    # Missing market days prevent a one-day change, but do not count towards MA windows.
    previous = out.groupby("segment").price.shift()
    out["change"] = (out.price / previous.where(previous.ne(0)) - 1)
    return out


def period_means(data, column, frequency, bounds):
    visible = data.loc[bounds[0]:bounds[1]].dropna(subset=[column])
    rows = []
    periods = visible.index.to_period(frequency)
    for (segment, period), group in visible.groupby(["segment", periods], sort=True):
        clipped = bounds[0] > period.start_time or bounds[1].normalize() < period.end_time.normalize()
        contract = ""
        if "delivery_start" in visible and visible.delivery_start.nunique() > 1:
            row = group.iloc[0]
            contract = "Contract  " + delivery_label(row.tenor, row.delivery_start)
        rows.append({
            "date": group.index[-1], "segment": segment,
            "price": group[column].mean(), "samples": len(group),
            "period": f"{period.start_time:%d %b %Y} – {period.end_time:%d %b %Y}",
            "observed": f"{group.index[0]:%d %b} – {group.index[-1]:%d %b %Y}",
            "clipped": "Selected dates cover part of this period" if clipped else "",
            "contract": contract,
        })
    return pd.DataFrame(rows, columns=["date", "segment", "price", "samples", "period", "observed", "clipped", "contract"])


def compare(a, b, divide, align, window):
    result = pd.DataFrame(index=a.index)
    result["a"] = a.price
    result["b"] = b.price
    result["a_period"] = [delivery_label(t, d) if pd.notna(t) else "—" for t, d in zip(a.tenor, a.delivery_start)]
    result["b_period"] = [delivery_label(t, d) if pd.notna(t) else "—" for t, d in zip(b.tenor, b.delivery_start)]
    result["segment"] = (a.segment.ne(a.segment.shift()) | b.segment.ne(b.segment.shift())).cumsum()
    valid = a.price.notna() & b.price.notna()
    if align:
        valid &= (a.tenor == b.tenor) & a.delivery_start.notna() & (a.delivery_start == b.delivery_start) & (a.delivery_end == b.delivery_end)
    value = a.price / b.price.where(b.price.ne(0)) if divide else a.price - b.price
    result["price"] = value.where(valid)
    roll = a.get("roll_from", pd.Series(index=a.index, dtype=object)).notna() | b.get("roll_from", pd.Series(index=b.index, dtype=object)).notna()
    result["roll_from"] = ("A: " + result.a_period.shift().fillna("—") + " / B: " + result.b_period.shift().fillna("—")).where(roll)
    result["roll_to"] = ("A: " + result.a_period + " / B: " + result.b_period).where(roll)
    observed = result.dropna(subset=["price"])
    grouped = observed.groupby("segment")
    rolling = grouped.price.rolling(window, min_periods=window)
    result["mean"] = rolling.mean().droplevel(0)
    result["p10"] = rolling.quantile(0.1).droplevel(0)
    result["p90"] = rolling.quantile(0.9).droplevel(0)
    std = rolling.std(ddof=0).droplevel(0).reindex(result.index)
    result["z"] = (result.price - result["mean"]) / std.where(std.ne(0))
    result["samples"] = (grouped.cumcount() + 1).clip(upper=window)
    return result


def snapshots(data, selected):
    available = data[data.quote_day.isin(selected)]
    expiries = pd.DatetimeIndex(sorted(available.delivery_start.dropna().unique()))
    if len(expiries) and available.tenor.nunique() == 1:
        frequency = {"month": "MS", "quarter": "QS-JAN", "year": "YS-JAN"}[available.tenor.iloc[0]]
        expiries = pd.date_range(expiries[0], expiries[-1], freq=frequency)
    return {
        quote_day: available[available.quote_day == quote_day].set_index("delivery_start").reindex(expiries)
        for quote_day in selected
    }



def monthly_spot(rows):
    """Arithmetic mean of stored daily Spot quotes, never forward-filled.

    Plot on each month's last observed quote date, so an unfinished month is not
    plotted in the future. Empty months remain explicit gaps. Coverage is metadata,
    not an assertion that all exchange trading days have been supplied.
    """
    values = rows.loc[rows.kind.eq("Spot")].set_index("quote_day").sort_index().price.dropna()
    grouped = values.resample("MS")
    out = grouped.agg(price="mean", samples="count")
    dates = pd.Series(values.index, index=values.index)
    out["first_day"] = dates.resample("MS").min()
    out["last_day"] = dates.resample("MS").max()
    out["month"] = out.index
    out.index = pd.DatetimeIndex(out.last_day.fillna(out["month"] + pd.offsets.MonthEnd()), name="quote_day")
    return out


def seasonal(data):
    """Align calendar month/day on a leap year; February 29 keeps its own position."""
    out = data.copy()
    out["quote_day"] = out.index
    out["year"] = out.index.year
    out["season_day"] = pd.to_datetime("2000-" + out.index.strftime("%m-%d"))
    return out
