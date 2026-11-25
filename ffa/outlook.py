"""Prospective fixed-contract BFA paths. No rolling targets or invented history."""
from hashlib import sha256
import json

import numpy as np
import pandas as pd

from .settlement import sessions

ENGINE = "bfa-path-v1"
WINDOWS = (5, 10, 20, 30, 60)
DEFAULTS = dict(window=20, weighting="Equal weighted", model="Reference mean",
                judgement="Neutral", strength=.3, maximum_pct=10., reason="", fade=True)


def future_grid(asof, expiry):
    """Indicative assessment dates; never assert a verified forward calendar."""
    asof, expiry = pd.Timestamp(asof).normalize(), pd.Timestamp(expiry).normalize()
    days, assumed = [], False
    if expiry <= asof:
        return pd.DatetimeIndex([]), assumed
    for month in pd.period_range(asof + pd.Timedelta(days=1), expiry, freq="M"):
        dates = sessions(month)
        if dates is None:
            dates = pd.bdate_range(month.start_time, month.end_time.normalize())
            assumed = True
        days.extend(dates[(dates > asof) & (dates <= expiry)])
    return pd.DatetimeIndex(days), assumed


def reference_values(values, window, weighting):
    values = np.asarray(values, dtype=float)[-window:]
    if len(values) < window or not np.isfinite(values).all():
        return None
    weights = np.ones(window)
    if weighting == "Recent weighted":
        weights = (1 - 2 / (window + 1)) ** np.arange(window-1, -1, -1)
    elif weighting != "Equal weighted":
        raise ValueError("Unknown reference weighting")
    return dict(level=float(np.average(values, weights=weights)), latest=float(values[-1]))


def forecast_values(reference, horizons, model="Reference mean"):
    horizons = np.asarray(horizons, dtype=float)
    if model == "Unchanged":
        return np.full(horizons.shape, reference["latest"])
    if model == "Reference mean":
        return np.full(horizons.shape, reference["level"])
    raise ValueError("Unknown BFA path model")


def contract_history(fixed, contract, asof=None):
    rows = fixed[fixed.kind.eq("BFA") & fixed.identity.eq(contract) & fixed.price.notna()].sort_values("quote_day")
    if rows.empty:
        return rows, pd.Series(dtype=float)
    if rows[["vessel", "product", "currency", "unit", "tenor", "delivery_start", "delivery_end"]].drop_duplicates().shape[0] != 1:
        raise ValueError("A BFA path must use one fixed contract and benchmark")
    if asof is None:
        asof = rows.quote_day.max()
    rows = rows[rows.quote_day.le(asof)].drop_duplicates("quote_day", keep="last")
    if rows.empty:
        return rows, pd.Series(dtype=float)
    first = rows.iloc[0]
    scope = fixed[fixed.vessel.eq(first.vessel) & fixed["product"].eq(first["product"]) &
                  fixed.currency.eq(first.currency) & fixed.unit.eq(first.unit)]
    days = pd.DatetimeIndex(sorted(scope.loc[scope.quote_day.between(rows.quote_day.min(), asof), "quote_day"].unique()))
    return rows, rows.set_index("quote_day").price.reindex(days)


def apply_judgement(path, config):
    sign = {"Neutral": 0., "Bearish": -1., "Bullish": 1.}[config.get("judgement", "Neutral")]
    strength, maximum = float(config.get("strength", .3)), float(config.get("maximum_pct", 10.))
    if not 0 <= strength <= 1 or not 0 <= maximum <= 100:
        raise ValueError("Invalid subjective adjustment")
    envelope = np.ones(len(path))
    until = config.get("judgement_until")
    if until:
        end = pd.Timestamp(until)
        dates = pd.DatetimeIndex(path.target_date)
        envelope = np.asarray(dates <= end, float)
        if config.get("fade", True):
            start = pd.Timestamp(config.get("judgement_from", dates.min()))
            denominator = max(1, (end-start).days + 1)
            envelope = np.clip(((end-dates).days.to_numpy()+1) / denominator, 0., 1.)
    return path.forecast.to_numpy() + sign * strength * maximum / 100 * path.forecast.abs().to_numpy() * envelope


def forecast_path(fixed, contract, config=None, asof=None):
    config = {**DEFAULTS, **(config or {})}
    rows, series = contract_history(fixed, contract, asof)
    if rows.empty:
        return pd.DataFrame(), {}
    asof, metadata = rows.quote_day.max(), rows.iloc[-1]
    reference = reference_values(series.dropna(), int(config["window"]), config["weighting"])
    if reference is None:
        return pd.DataFrame(), dict(reason=f"Need {config['window']} valid quotes for this fixed contract. Try a shorter reference window.")
    days, assumed = future_grid(asof, metadata.delivery_end)
    horizons = np.arange(1, len(days)+1)
    output = pd.DataFrame(dict(target_date=days, horizon=horizons,
                              forecast=forecast_values(reference, horizons, config["model"]),
                              unchanged=reference["latest"], reference_mean=reference["level"]))
    if not output.empty:
        output["scenario"] = apply_judgement(output, config)
    info = dict(asof=asof, expiry=metadata.delivery_end, tenor=metadata.tenor, delivery_start=metadata.delivery_start,
                vessel=metadata.vessel, product=metadata["product"], currency=metadata.currency, unit=metadata.unit,
                **reference, assumed_calendar=assumed, config=config)
    return output, info


def data_revision(rows):
    fields = ["identity", "quote_day", "price"]
    content = rows[fields].sort_values(["identity", "quote_day"]).to_csv(index=False, lineterminator="\n")
    return sha256(content.encode()).hexdigest()[:20]


def prediction_record(path, info, contract, config, source_revision, created_at=None):
    created = pd.Timestamp(created_at) if created_at is not None else pd.Timestamp.now(tz="UTC")
    created = created.tz_localize("UTC") if created.tzinfo is None else created.tz_convert("UTC")
    # A newly issued record can never assert that it predicted past target dates.
    future = path[path.target_date.gt(created.tz_localize(None).normalize())]
    if future.empty or pd.Timestamp(info["asof"]).date() > created.date():
        return None
    key = dict(engine=ENGINE, contract=contract, config=config, source_revision=source_revision,
               asof=str(pd.Timestamp(info["asof"]).date()), issued_on=str(created.date()))
    identity = sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:24]
    points = [dict(target_date=str(row.target_date.date()), horizon=int(row.horizon), forecast=float(row.forecast),
                   scenario=float(row.scenario), unchanged=float(row.unchanged), reference_mean=float(row.reference_mean))
              for row in future.itertuples()]
    return dict(id=identity, created_at=created.isoformat(), **key,
                vessel=info["vessel"], product=info["product"], tenor=info["tenor"],
                delivery_start=str(pd.Timestamp(info["delivery_start"]).date()),
                expiry=str(pd.Timestamp(info["expiry"]).date()), anchor=float(info["latest"]),
                indicative_calendar=True, points=points)


def evaluated_record(record, fixed, through=None):
    rows = fixed[fixed.identity.eq(record["contract"]) & fixed.kind.eq("BFA")].sort_values("quote_day")
    if through is not None:
        rows = rows[rows.quote_day.le(through)]
    actual = rows.drop_duplicates("quote_day", keep="last").set_index("quote_day").price
    path = pd.DataFrame(record["points"])
    path["target_date"] = pd.to_datetime(path.target_date)
    path["actual"] = path.target_date.map(actual)
    path["error"] = path.scenario - path.actual
    return path


def validation_errors(fixed, contract, config, horizons, asof=None):
    """Past-origin errors; same tenor and exact months to delivery, one case/date.

    This validates an explicitly configured baseline. No parameter is selected
    from these scores, and unverified archived vintages are not strict PIT data.
    """
    selected, _ = contract_history(fixed, contract, asof)
    if selected.empty:
        return pd.DataFrame()
    current = selected.iloc[-1]
    asof = current.quote_day
    offset = (current.delivery_start.year-asof.year)*12 + current.delivery_start.month-asof.month
    scope = fixed[fixed.vessel.eq(current.vessel) & fixed["product"].eq(current["product"]) &
                  fixed.currency.eq(current.currency) & fixed.unit.eq(current.unit) & fixed.quote_day.le(asof)]
    days = pd.DatetimeIndex(sorted(scope.quote_day.unique()))
    candidates = scope[scope.kind.eq("BFA") & scope.tenor.eq(current.tenor) &
                       (scope.delivery_end.lt(asof) | scope.identity.eq(contract))]
    horizon_set = sorted({int(h) for h in horizons if int(h) > 0})
    result = []
    window = int(config["window"])
    for identity, group in candidates.groupby("identity", sort=False):
        first = group.iloc[0]
        prices = group.drop_duplicates("quote_day", keep="last").set_index("quote_day").price.reindex(days)
        prices = prices.where(prices.index <= first.delivery_end)
        values = prices.to_numpy(float)
        if len(values) < window:
            continue
        observed = np.flatnonzero(np.isfinite(values))
        if len(observed) < window:
            continue
        rolling = np.lib.stride_tricks.sliding_window_view(values[observed], window)
        weights = np.ones(window)
        if config["weighting"] == "Recent weighted":
            weights = (1-2/(window+1)) ** np.arange(window-1,-1,-1)
        levels = np.full(len(values), np.nan)
        levels[observed[window-1:]] = rolling @ (weights/weights.sum())
        offsets = (first.delivery_start.year-days.year)*12+first.delivery_start.month-days.month
        anchors = np.flatnonzero(np.isfinite(levels) & (offsets == offset))
        for h in horizon_set:
            starts = anchors[anchors+h < len(days)]
            starts = starts[np.isfinite(values[starts+h])]
            for i in starts:
                ref = dict(latest=values[i], level=levels[i])
                predicted = forecast_values(ref, [h], config["model"])[0]
                result.append(dict(date=days[i], target_date=days[i+h], contract=identity, delivery_start=first.delivery_start,
                                   horizon=h, actual=values[i+h], forecast=predicted,
                                   unchanged=values[i], reference_mean=ref["level"], error=values[i+h]-predicted))
    return pd.DataFrame(result)


def independent_errors(errors, horizon):
    if errors.empty:
        return errors.copy()
    rows = errors[errors.horizon.eq(horizon)].sort_values(["date", "delivery_start", "contract"]).drop_duplicates("date")
    indices, next_date = [], pd.Timestamp.min
    for index, row in rows.iterrows():
        if row.date >= next_date:
            indices.append(index)
            next_date = row.target_date
    return rows.loc[indices].copy()
