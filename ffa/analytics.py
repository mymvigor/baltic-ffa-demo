import pandas as pd

from .model import label

FIELDS = ["identity", "quote_day", "vessel", "product", "kind", "currency", "unit", "tenor",
          "delivery_start", "delivery_end", "rolling", "mapping", "source", "price"]


def frame(rows):
    result = pd.DataFrame(rows, columns=FIELDS)
    result["price"] = result["price"].astype(float)
    for field in ("quote_day", "delivery_start", "delivery_end"):
        result[field] = pd.to_datetime(result[field])
    return result


def unit_label(currency, unit):
    return f"{currency}/{unit}" if currency else "PTS"


def delivery_label(tenor, start):
    return "现货" if tenor == "spot" else label(tenor, None if pd.isna(start) else start)


def options(fixed, rolling):
    eligible = fixed[(fixed.kind == "Spot") | fixed.delivery_start.notna()]
    series = {}
    for _, row in eligible.sort_values(["kind", "tenor", "delivery_start"]).drop_duplicates("identity").iterrows():
        name = delivery_label(row.tenor, row.delivery_start)
        key = (row.vessel, row["product"], row.kind, row.currency, row.unit, row.tenor, "Fixed", row.identity)
        series[key] = (name, eligible[eligible.identity == row.identity])
    fields = ["vessel", "product", "kind", "currency", "unit", "tenor", "rolling"]
    for components, data in rolling.groupby(fields, sort=True):
        *base, code = components
        unresolved = data.delivery_start.isna().any()
        multiple = data.duplicated("quote_day", keep=False)
        if multiple.any():
            # Multiple supplier series for a rolling code must remain independent.
            for index, (_, sub) in enumerate(data.groupby("series"), 1):
                if not sub.duplicated("quote_day").any():
                    series[(*base, "Rolling", f"{code}#{index}")] = (f"{code} · 系列 {index}", sub)
        else:
            name = f"{code} · 交割期未确认" if unresolved else code
            series[(*base, "Rolling", code)] = (name, data)
    return series


def timeline(rows, days):
    out = rows.set_index("quote_day").sort_index().reindex(days)
    known = out.delivery_start.dt.strftime("%Y-%m-%d").fillna("")
    spot = out.kind.eq("Spot")
    observed = out.price.notna()
    # An unresolved quote has no demonstrably continuous delivery segment.
    tokens = known.where(known.ne(""), pd.Series(days.strftime("unknown-%Y-%m-%d"), index=days))
    tokens = tokens.mask(spot, "spot").where(observed).ffill()
    out["segment"] = tokens.ne(tokens.shift()).cumsum()
    return out


def indicators(data):
    out = data.copy()
    for column in ("ma5", "ma20", "upper", "lower", "change"):
        out[column] = float("nan")
    for _, group in out.groupby("segment"):
        values = group.price.dropna()
        out.loc[values.index, "ma5"] = values.rolling(5).mean()
        mean = values.rolling(20).mean()
        deviation = values.rolling(20).std(ddof=0)
        out.loc[values.index, "ma20"] = mean
        out.loc[values.index, "upper"] = mean + 2 * deviation
        out.loc[values.index, "lower"] = mean - 2 * deviation
        # A missing observed market day also prevents a one-day change.
        out.loc[group.index, "change"] = group.price.pct_change(fill_method=None).where(group.price.shift().ne(0))
    return out


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
    for column in ("mean", "p10", "p90", "z", "samples"):
        result[column] = float("nan")
    for _, group in result.groupby("segment"):
        values = group.price.dropna()
        rolling = values.rolling(window)
        mean = rolling.mean()
        std = rolling.std(ddof=0)
        result.loc[values.index, "mean"] = mean
        result.loc[values.index, "p10"] = rolling.quantile(0.1)
        result.loc[values.index, "p90"] = rolling.quantile(0.9)
        result.loc[values.index, "z"] = (values - mean) / std.where(std.ne(0))
        result.loc[values.index, "samples"] = values.expanding().count().clip(upper=window)
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

