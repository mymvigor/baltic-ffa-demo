"""Seasonal settlement forecasts. All historical features are cut at their issue date."""
import numpy as np
import pandas as pd

WINDOWS = (5, 10, 20, 30, 60, "YTD", "Custom")
BASIC = ("Difference", "Ratio")
# Baltic dry index non-publication calendars, 2026 and 2027.
HOLIDAYS = {2026: ("01-01", "04-03", "04-06", "05-04", "05-25", "08-31"),
            2027: ("01-01", "03-26", "03-29", "05-03", "05-31", "08-30")}
SUPPORTED = {"P5TC", "C5TC (182)", "C5TC", "S11TC", "S10TC", "HS7TC", "H7TC"}
FACTORS = {
    "Spot trend": ("Recent momentum may persist. Latest Spot less its five-session mean. Near months; five valid quotes. Reversals can erase the signal.", "trend"),
    "Market strength": ("A persistent recent repricing may carry forward. Five-session mean less 20-session mean. Near months; 20 quotes. Often overlaps momentum.", "strength"),
    "Seasonal deviation": ("Seasonal shocks may persist or revert. Current minus historical reference (A − B). Near and medium months; valid seasonal history. May overlap the base forecast.", "seasonal"),
    "Volatility": ("Volatile markets may behave differently. Standard deviation of 19 consecutive observed price changes. Near months; 20 quotes. Has no inherent direction.", "volatility"),
}


def sessions(month):
    month = pd.Period(month, freq="M")
    if month.year not in HOLIDAYS:
        return None
    closed = set(HOLIDAYS[month.year]) | {f"12-{d}" for d in range(25, 32)}
    days = pd.bdate_range(month.start_time, month.end_time.normalize())
    return days[~days.strftime("%m-%d").isin(closed)]


def spot_history(quotes, cutoff=None):
    data = quotes[quotes.kind.eq("Spot") & quotes.price.notna()]
    if cutoff is not None:
        cutoff = pd.Timestamp(cutoff)
        available = pd.to_datetime(data.available_at, utc=True, errors="coerce")
        data = data[data.quote_day.le(cutoff) & available.lt(cutoff.tz_localize("UTC") + pd.Timedelta(days=1))]
    return data.sort_values("quote_day").drop_duplicates("quote_day").set_index("quote_day").price


def shift_year(day, year):
    return pd.Timestamp(day) + pd.DateOffset(years=year - day.year)


def month_actual(spot, month, asof):
    month = pd.Period(month, freq="M")
    end = month.end_time.normalize()
    if end >= asof:
        return None
    actual = spot.loc[month.start_time:end]
    expected = sessions(month)
    if expected is not None:
        actual = actual.reindex(expected)
        if actual.empty or actual.isna().any():
            return None
    elif len(actual) < 15 or actual.index[0].day > 8 or (end - actual.index[-1]).days > 8:
        return None
    return float(actual.mean()) if not actual.empty else None


def reference(spot, cutoff, window, custom=None):
    if window == "YTD":
        return spot.loc[pd.Timestamp(cutoff.year, 1, 1):cutoff]
    if window == "Custom":
        start, end = custom
        return spot.loc[start:min(end, cutoff)]
    sample = spot.loc[:cutoff].tail(window)
    return sample.iloc[:0] if len(sample) != window else sample


def bfa_history(quotes):
    """Monthly fixed contracts only; callers supply one benchmark and unit."""
    rows = quotes[quotes.kind.eq("BFA") & quotes.tenor.eq("month") & quotes.price.notna()].copy()
    return {pd.Period(month, "M"): part.sort_values("quote_day").drop_duplicates("quote_day", keep="last")
            for month, part in rows.groupby("delivery_start")}


def _bfa_reference(curves, month, cutoff, window, custom, strict):
    rows = curves.get(month)
    if rows is None:
        return pd.Series(dtype=float)
    rows = rows[rows.quote_day.le(cutoff)]
    if strict:
        cutoff_utc = cutoff.tz_localize("UTC") + pd.Timedelta(days=1)
        rows = rows[pd.to_datetime(rows.available_at, utc=True, errors="coerce").lt(cutoff_utc)]
    return reference(rows.set_index("quote_day").price, cutoff, window, custom)


def forecast(spot, issue, month, years, window=20, custom=None, *, basis="Spot", curves=None, strict=False):
    issue, month = pd.Timestamp(issue), pd.Period(month, freq="M")
    visible = spot.loc[:issue]
    current = (_bfa_reference(curves or {}, month, issue, window, custom, strict)
               if basis == "BFA" else reference(visible, issue, window, custom))
    anchor = min(custom[1], issue) if window == "Custom" and custom else issue
    if current.empty or (anchor - current.index[-1]).days > 8:
        return None
    a = float(current.mean())
    analogues = []
    for year in years:
        if year >= issue.year:
            continue
        historical_end = shift_year(issue, year)
        historical_custom = None
        if custom is not None:
            historical_custom = tuple(shift_year(day, day.year + year - issue.year) for day in custom)
        delivery = month - 12 * (issue.year - year)
        ref = (_bfa_reference(curves or {}, delivery, historical_end, window, historical_custom, strict)
               if basis == "BFA" else reference(visible, historical_end, window, historical_custom))
        c = month_actual(visible, delivery, issue)
        # Require a completed analogue year and seasonal reference anchored near its end date.
        anchor = historical_custom[1] if window == "Custom" else historical_end
        if ref.empty or c is None or (anchor - ref.index[-1]).days > 8:
            continue
        if window in ("YTD", "Custom") and len(ref) < max(1, int(len(current) * .75)):
            continue
        analogues.append((year, float(ref.mean()), c))
    if not analogues:
        return None
    b = float(np.mean([item[1] for item in analogues]))
    c = float(np.mean([item[2] for item in analogues]))
    remaining = {"Difference": c + a - b, "Ratio": c * a / b if b > 1e-8 else np.nan,
                 "Spot": float(visible.iloc[-1])}
    expected = sessions(month)
    fixed_sum, fixed, left = 0., 0, None
    if expected is not None:
        published = expected[expected <= issue]
        known = visible.reindex(published)
        if known.isna().any():
            return None
        fixed_sum, fixed, left = float(known.sum()), len(known), len(expected) - len(known)
    elif month.start_time <= issue:
        # Never guess a denominator for an in-progress settlement.
        return None
    def blend(value):
        if left is None or not fixed:
            return value
        return (fixed_sum + left * value) / (fixed + left)
    if basis == "BFA":
        # BFA quotes already estimate the full monthly settlement. Do not blend
        # known Spot into them twice. Invert to the implied remaining level so
        # later scenario changes still leave observed settlement sessions fixed.
        for name in BASIC:
            if left == 0:
                remaining[name] = fixed_sum / fixed
            elif fixed and left is not None:
                remaining[name] = ((fixed + left) * remaining[name] - fixed_sum) / left
    tail = visible.tail(20)
    factors = {"seasonal": a - b, "trend": np.nan, "strength": np.nan, "volatility": np.nan}
    if len(tail) >= 5:
        factors["trend"] = float(tail.iloc[-1] - tail.tail(5).mean())
    if len(tail) == 20:
        factors["strength"] = float(tail.tail(5).mean() - tail.mean())
        factors["volatility"] = float(tail.diff().std())
    return dict(A=a, B=b, C=c, years=[item[0] for item in analogues], fixed=fixed, left=left,
                remaining=remaining, values={name: blend(val) for name, val in remaining.items()},
                multiplier=1. if left is None else left / (fixed + left), factors=factors)


def subjective_adjustment(prediction, base, direction, strength, maximum_pct):
    """An explicit scenario, not a fitted coefficient or probability."""
    sign = {"Bearish": -1., "Neutral": 0., "Bullish": 1.}[direction]
    if not 0 <= strength <= 1 or not 0 <= maximum_pct <= 100:
        raise ValueError("Scenario strength or maximum adjustment is out of range")
    return sign * strength * maximum_pct / 100 * abs(prediction["remaining"][base]) * prediction["multiplier"]


def eligible_years(spot, issue):
    history = spot.loc[:issue]
    years = []
    for year, rows in history.groupby(history.index.year):
        if year < issue.year and len(rows) >= 200 and rows.index.month.nunique() == 12:
            years.append(int(year))
    return sorted(years, reverse=True)


def daily_forecasts(quotes, contract, target, asof, years, window=20, custom=None, strict=False,
                    factors=(), weights=(), basis="Spot"):
    """Fixed-target daily reconstruction; final actuals are comparison labels only."""
    target, asof = pd.Period(target, "M"), pd.Timestamp(asof)
    spot = spot_history(quotes)
    snapshots = quotes[quotes.kind.eq("BFA") & quotes.identity.eq(contract) & quotes.quote_day.le(asof)]
    if snapshots.empty:
        return pd.DataFrame()
    dates = spot.index[(spot.index >= snapshots.quote_day.min()) &
                      (spot.index <= min(asof, target.end_time.normalize()))]
    monthly = snapshots.tenor.eq("month").all()
    actual = month_actual(spot_history(quotes, asof) if strict else spot, target, asof)
    curves = bfa_history(quotes) if basis == "BFA" else None
    output = []
    for date in dates:
        if strict:
            cutoff = date.tz_localize("UTC") + pd.Timedelta(days=1)
            known = snapshots[snapshots.quote_day.le(date)]
            if not pd.to_datetime(known.available_at, utc=True, errors="coerce").lt(cutoff).any():
                continue
        visible = spot_history(quotes, date) if strict else spot
        allowed = set(eligible_years(visible, date))
        # Keep selected calendar years, never import years unavailable on this date.
        usable = [year for year in years if year in allowed]
        if custom and custom[1] > date:
            continue
        prediction = forecast(visible, date, target, usable, window, custom, basis=basis, curves=curves, strict=strict)
        if prediction is None:
            continue
        observed = snapshots[snapshots.quote_day.eq(date)] if monthly else snapshots.iloc[:0]
        if strict:
            cutoff = date.tz_localize("UTC") + pd.Timedelta(days=1)
            observed = observed[pd.to_datetime(observed.available_at, utc=True, errors="coerce").lt(cutoff)]
        row = dict(date=date, BFA=float(observed.price.iloc[-1]) if not observed.empty else np.nan,
                   Actual=np.nan if actual is None else actual, **prediction["values"])
        if factors:
            x = np.asarray([prediction["factors"][factor] for factor in factors])
            row["correction"] = float(x @ np.asarray(weights)) * prediction["multiplier"]
        output.append(row)
    return pd.DataFrame(output)


def replay(quotes, issue, target, windows, offsets, custom=None, strict=False, basis="Spot"):
    """Matched monthly targets, one BFA snapshot per historical contract."""
    spot = spot_history(quotes)
    curves = bfa_history(quotes) if basis == "BFA" else None
    available_spot = quotes.loc[quotes.kind.eq("Spot") & quotes.price.notna()].sort_values("quote_day")
    published = available_spot.drop_duplicates("quote_day").set_index("quote_day").available_at
    forward = quotes[quotes.kind.eq("BFA") & quotes.tenor.eq("month") &
                     quotes.delivery_start.lt(target.start_time) & quotes.delivery_end.lt(issue)]
    distance = target.ordinal - issue.to_period("M").ordinal
    result = []
    for delivery, rows in forward.groupby("delivery_start"):
        month = delivery.to_period("M")
        ref_month = month - distance
        daynum = min(issue.day, ref_month.days_in_month)
        anchor = pd.Timestamp(ref_month.year, ref_month.month, daynum)
        row = rows[rows.quote_day.le(anchor) & rows.quote_day.ge(ref_month.start_time)].sort_values("quote_day").tail(1)
        actual = month_actual(spot, month, issue)
        if row.empty or actual is None:
            continue
        date = row.quote_day.iloc[0]
        if (anchor - date).days > 7:
            continue
        training_spot = spot_history(quotes, date) if strict else spot
        if strict:
            available = pd.to_datetime(row.available_at.iloc[0], utc=True)
            if pd.isna(available) or available >= date.tz_localize("UTC") + pd.Timedelta(days=1):
                continue
        eligible = set(eligible_years(training_spot, date))
        years = [date.year - lag for lag in offsets if date.year - lag in eligible]
        target_available = pd.NaT
        if strict:
            expected = sessions(month)
            if expected is None:
                continue
            stamps = pd.to_datetime(published.reindex(expected), utc=True, errors="coerce")
            if stamps.isna().any():
                continue
            target_available = stamps.max()
        dates = tuple(shift_year(d, d.year + date.year - issue.year) for d in custom) if custom else None
        for window in windows:
            prediction = forecast(training_spot, date, month, years, window, dates,
                                  basis=basis, curves=curves, strict=strict)
            if prediction is None:
                continue
            result.append(dict(date=date, delivery=month.start_time, matured=month.end_time.normalize(),
                               window=str(window), Actual=actual, BFA=float(row.price.iloc[0]), target_available=target_available,
                               **prediction["values"], **prediction["factors"], remaining_weight=prediction["multiplier"]))
    return pd.DataFrame(result)


def metrics(rows, models):
    actual = rows.Actual.to_numpy(float)
    results = []
    for model in models:
        error = rows[model].to_numpy(float) - actual
        baseline = np.abs(rows.BFA - actual).mean()
        mae = float(np.abs(error).mean())
        denominator = np.square(actual - actual.mean()).sum()
        results.append(dict(model=model, mae=mae, bias=float(error.mean()), n=len(error),
                            r2=1 - np.square(error).sum() / denominator if denominator > 0 else np.nan,
                            improvement=(baseline - mae) / baseline if baseline > 0 else np.nan))
    return pd.DataFrame(results)


def fit_adjustment(train, base, factors, method):
    """Train slopes on past errors only, with train-only scaling and rank checks."""
    train = train.dropna(subset=[base, "Actual", *factors])
    minimum = max(24, 8 * (len(factors) + 1))
    if train.delivery.nunique() < minimum or not factors:
        return None
    x = train[factors].to_numpy(float)
    center, scale = x.mean(axis=0), x.std(axis=0)
    if (scale < 1e-8).any():
        return None
    z = (x - center) / scale
    design = z * train.remaining_weight.to_numpy()[:, None]
    if np.linalg.cond(design) > 20:
        return None
    y = (train.Actual - train[base]).to_numpy(float)
    alpha = 10 if method == "Ridge" else 0
    coefficients = np.linalg.solve(design.T @ design + alpha * np.eye(len(factors)), design.T @ y)
    return center, scale, coefficients


def exploratory_replay(rows, base, factors, method, weights, strict=False):
    output = rows.copy()
    output["Exploratory"] = np.nan
    for idx, row in rows.sort_values("date").iterrows():
        x = row[factors].to_numpy(float)
        if not np.isfinite(x).all():
            continue
        if method == "Manual":
            correction = x @ np.asarray(weights)
        else:
            train = rows[rows.matured.lt(row.date)]
            if strict:
                train = train[train.target_available.lt(row.date.tz_localize("UTC") + pd.Timedelta(days=1))]
            fit = fit_adjustment(train, base, factors, method)
            if fit is None:
                continue
            center, scale, coefs = fit
            correction = ((x - center) / scale) @ coefs
        output.at[idx, "Exploratory"] = row[base] + correction * row.remaining_weight
    return output
