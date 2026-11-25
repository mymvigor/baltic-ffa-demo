"""Prospective fixed-contract BFA paths. No rolling targets or invented history."""
from hashlib import sha256
import json

import numpy as np
import pandas as pd

from .settlement import sessions

ENGINE = "seasonal-path-v5"
WINDOWS = (5, 10, 20, 30, 60)
DEFAULTS = dict(window=20, weighting="Equal weighted", model="Seasonal difference",
                judgement="Neutral", strength=.3, maximum_pct=10., reason="", fade=True)


def future_grid(asof, expiry, kind="Spot"):
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
        elif kind=="BFA":
            # BFA continues over the dry-index Christmas closure. Its own
            # published holidays differ from the underlying Spot calendar.
            from .settlement import HOLIDAYS
            christmas={2026:("12-25","12-28"),2027:("12-27","12-28")}
            closed=set(HOLIDAYS[month.year]) | set(christmas[month.year])
            dates=pd.bdate_range(month.start_time,month.end_time.normalize())
            dates=dates[~dates.strftime("%m-%d").isin(closed)]
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


def _catalogue(fixed, window, weighting):
    prepared = {}
    for identity, group in fixed[fixed.kind.eq("BFA") & fixed.price.notna()].groupby("identity", sort=False):
        row = group.iloc[0]
        if pd.isna(row.delivery_start) or pd.isna(row.delivery_end):
            continue
        series = group.sort_values("quote_day").drop_duplicates("quote_day", keep="last").set_index("quote_day").price
        series = series.loc[:row.delivery_end]
        if len(series) < window:
            continue
        weights = np.ones(window)
        if weighting == "Recent weighted":
            weights = (1-2/(window+1)) ** np.arange(window-1,-1,-1)
        levels = np.lib.stride_tricks.sliding_window_view(series.to_numpy(float),window) @ (weights/weights.sum())
        means = pd.Series(levels,index=series.index[window-1:])
        key = (row.tenor,pd.Timestamp(row.delivery_start),pd.Timestamp(row.delivery_end))
        previous = prepared.get(key)
        if previous is None or len(series) > len(previous[1]):
            prepared[key] = (identity,series,means)
    return prepared


def _analogues(catalogue, metadata, origin, years=None, custom=None, weighting="Equal weighted"):
    matches = []
    for (tenor,start,end),(identity,series,means) in catalogue.items():
        if tenor != metadata.tenor or start.month != metadata.delivery_start.month or end.month != metadata.delivery_end.month:
            continue
        lag = metadata.delivery_start.year-start.year
        year = origin.year-lag
        if lag <= 0 or tenor != metadata.tenor or end >= origin or (years is not None and year not in years):
            continue
        expected_end=metadata.delivery_end-pd.DateOffset(years=lag)
        if metadata.delivery_end.is_month_end:
            expected_end+=pd.offsets.MonthEnd(0)
        if start != metadata.delivery_start-pd.DateOffset(years=lag) or end != expected_end:
            continue
        cutoff = origin-pd.DateOffset(years=lag)
        if custom:
            sample=series.loc[pd.Timestamp(custom[0])-pd.DateOffset(years=lag):min(cutoff,pd.Timestamp(custom[1])-pd.DateOffset(years=lag))]
            if sample.empty:
                continue
            value=reference_values(sample,len(sample),weighting)["level"]
        else:
            index=means.index.searchsorted(cutoff,side="right")-1
            if index<0 or (cutoff-means.index[index]).days>8:continue
            value=float(means.iloc[index])
        matches.append(dict(year=int(year),lag=int(lag),contract=identity,series=series,reference=value))
    return sorted(matches,key=lambda row:row["year"],reverse=True)


def _aligned_values(series, targets):
    # Read a real nearby historical quote within the same calendar month.
    # This aligns weekends/leap years for constructing predictions; it never
    # fills or changes the actual-price series.
    index = series.index.as_unit("ns")
    targets = pd.DatetimeIndex(targets).as_unit("ns")
    right = np.clip(index.searchsorted(targets,side="left"),0,len(index)-1)
    left = np.clip(right-1,0,len(index)-1)
    left_distance = abs(index.asi8[left]-targets.asi8)
    right_distance = abs(index.asi8[right]-targets.asi8)
    left_valid = (index[left].year == targets.year) & (index[left].month == targets.month)
    right_valid = (index[right].year == targets.year) & (index[right].month == targets.month)
    left_distance = np.where(left_valid,left_distance,np.iinfo(np.int64).max)
    right_distance = np.where(right_valid,right_distance,np.iinfo(np.int64).max)
    chosen = np.where(left_distance <= right_distance,left,right)
    dates = index[chosen]
    valid = (abs(dates.asi8-targets.asi8) <= pd.Timedelta(days=3).value) & (dates.year == targets.year) & (dates.month == targets.month)
    valid &= (targets>=index.min()) & (targets<=index.max())
    return np.where(valid,series.to_numpy(float)[chosen],np.nan)


def _seasonal_points(targets, reference, analogues, model="Difference", stable=False, return_years=False):
    totals, bases, counts = np.zeros(len(targets)),np.zeros(len(targets)),np.zeros(len(targets),dtype=int)
    memberships=[[] for _ in targets]
    for analogue in analogues:
        historical = pd.DatetimeIndex(targets)-pd.DateOffset(years=analogue["lag"])
        values = _aligned_values(analogue["series"],historical)
        valid = np.isfinite(values)
        totals += np.where(valid,values,0.)
        bases += valid*analogue["reference"]
        counts += valid
        for i in np.flatnonzero(valid):memberships[i].append(int(analogue["year"]))
    mean = np.divide(totals,counts,out=np.full(len(targets),np.nan),where=counts>0)
    base = np.divide(bases,counts,out=np.full(len(targets),np.nan),where=counts>0)
    predicted = mean*reference/np.where(base>1e-8,base,np.nan) if model == "Ratio" else mean+reference-base
    if stable:
        predicted = np.where(counts==len(analogues),predicted,np.nan)
    result=(predicted,mean,base,counts)
    return (*result,[",".join(map(str,years)) for years in memberships]) if return_years else result


def forecast_path(fixed,contract,config=None,asof=None,*,_prepared=None):
    config = {**DEFAULTS, **(config or {})}
    config["model"] = "Ratio" if config.get("model") == "Ratio" else "Difference"
    if config.get("target_kind") == "Spot":
        return spot_forecast_path(fixed,config,asof)
    rows, series = contract_history(fixed, contract, asof)
    if rows.empty:
        return pd.DataFrame(), {}
    asof, metadata = rows.quote_day.max(), rows.iloc[-1]
    if config.get("year_mode")=="Rolling years" and config.get("years"):
        shift=asof.year-int(config.get("year_anchor",asof.year))
        config["years"]=[int(y)+shift for y in config["years"]]
        config["year_anchor"]=asof.year
    sample=series.dropna()
    if config.get("custom"):
        sample=sample.loc[pd.Timestamp(config["custom"][0]):min(asof,pd.Timestamp(config["custom"][1]))]
    reference = reference_values(sample,len(sample) if config.get("custom") else int(config["window"]),config["weighting"]) if len(sample) else None
    if reference is None:
        return pd.DataFrame(), dict(reason=f"Need {config['window']} valid quotes for this fixed contract. Try a shorter reference window.")
    scope = fixed[fixed.vessel.eq(metadata.vessel) & fixed["product"].eq(metadata["product"]) &
                  fixed.currency.eq(metadata.currency) & fixed.unit.eq(metadata.unit) & fixed.quote_day.le(asof)]
    catalogue = _prepared if _prepared is not None else _catalogue(scope,int(config["window"]),config["weighting"])
    available = _analogues(catalogue,metadata,asof,custom=config.get("custom"),weighting=config["weighting"])
    candidates = [item["year"] for item in available]
    years = config.get("years")
    analogues = [item for item in available if item["year"] in years] if years is not None else available[:5]
    if not analogues:
        return pd.DataFrame(),dict(reason="No completed historical matching contracts have the required reference quotes. Choose another contract or a shorter window.",candidate_years=candidates)
    expiry=metadata.delivery_end
    closing=sessions(expiry.to_period("M"))
    if closing is not None:
        expiry=min(expiry,closing[-1])
    days, assumed = future_grid(asof, expiry,"BFA")
    if config.get("forecast_end"):
        days = days[days<=pd.Timestamp(config["forecast_end"])]
    if config.get("_target_dates"):
        days=pd.DatetimeIndex(pd.to_datetime(config["_target_dates"]))
        days=days[(days>asof)&(days<=expiry)]
    if not len(days):
        return pd.DataFrame(),dict(reason=f"No quotation dates remain after {asof:%d %b %Y} and before the contract's final settlement window. Use a review view for historical predictions.",candidate_years=candidates)
    values,historical,base,counts,members = _seasonal_points(days,reference["level"],analogues,config["model"],return_years=True)
    output = pd.DataFrame(dict(target_date=days,horizon=np.arange(1,len(days)+1),forecast=values,
                              historical_mean=historical,historical_reference=base,historical_n=counts,historical_years=members,
                              unchanged=reference["latest"],reference_mean=reference["level"]))
    if not output.empty:
        output["scenario"] = apply_judgement(output,config)
    if output.empty or not np.isfinite(output.forecast).any():
        if config['model']=='Ratio' and np.any((counts>0)&(base<=1e-8)):
            return pd.DataFrame(),dict(reason="Ratio requires a positive, non-zero historical reference mean. Use Difference for this selection.",candidate_years=candidates)
        return pd.DataFrame(),dict(reason="No aligned historical future quotes are available for this contract.",candidate_years=candidates)
    source_contracts = [contract,*[item["contract"] for item in analogues]]
    info = dict(asof=asof,expiry=expiry,tenor=metadata.tenor,delivery_start=metadata.delivery_start,
                vessel=metadata.vessel,product=metadata["product"],currency=metadata.currency,unit=metadata.unit,
                **reference,assumed_calendar=assumed,config=config,candidate_years=candidates,
                reference_years=[item["year"] for item in analogues],
                historical_reference=float(np.mean([item["reference"] for item in analogues])),
                source_revision="" if config.get("_replay") else data_revision(scope[scope.identity.isin(source_contracts)]))
    return output,info


def data_revision(rows):
    fields = ["identity", "quote_day", "price"]
    content = rows[fields].sort_values(["identity", "quote_day"]).to_csv(index=False, lineterminator="\n")
    return sha256(content.encode()).hexdigest()[:20]


def prediction_record(path, info, contract, config, source_revision, created_at=None):
    created = pd.Timestamp(created_at) if created_at is not None else pd.Timestamp.now(tz="UTC")
    created = created.tz_localize("UTC") if created.tzinfo is None else created.tz_convert("UTC")
    # A newly issued record can never assert that it predicted past target dates.
    future = path[path.target_date.gt(created.tz_localize(None).normalize())].dropna(subset=["forecast","scenario"])
    if future.empty or pd.Timestamp(info["asof"]).date() > created.date():
        return None
    key = dict(engine=ENGINE, contract=contract, config=config, source_revision=source_revision,
               asof=str(pd.Timestamp(info["asof"]).date()), issued_on=str(created.date()))
    identity = sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:24]
    points = [dict(target_date=str(row.target_date.date()), horizon=int(row.horizon), forecast=float(row.forecast),
                   scenario=float(row.scenario), unchanged=float(row.unchanged), reference_mean=float(row.reference_mean),historical_mean=float(row.historical_mean),
                   historical_reference=float(row.historical_reference),historical_n=int(row.historical_n),historical_years=getattr(row,"historical_years",""))
              for row in future.itertuples()]
    return dict(id=identity, created_at=created.isoformat(), **key,
                vessel=info["vessel"], product=info["product"], tenor=info["tenor"],
                delivery_start=str(pd.Timestamp(info["delivery_start"]).date()),
                expiry=str(pd.Timestamp(info["expiry"]).date()), anchor=float(info["latest"]),
                indicative_calendar=True, reference_years=info.get("reference_years",[]), points=points)


def evaluated_record(record, fixed, through=None):
    rows = fixed[fixed.kind.eq("Spot") & fixed.vessel.eq(record["vessel"]) & fixed["product"].eq(record["product"])].sort_values("quote_day") if record.get("config",{}).get("target_kind") == "Spot" else fixed[fixed.identity.eq(record["contract"]) & fixed.kind.eq("BFA")].sort_values("quote_day")
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
    days = pd.DatetimeIndex(sorted(scope.loc[scope.kind.eq("BFA"),"quote_day"].unique()))
    candidates = scope[scope.kind.eq("BFA") & scope.tenor.eq(current.tenor) &
                       (scope.delivery_end.lt(asof) | scope.identity.eq(contract))]
    horizon_set = sorted({int(h) for h in horizons if int(h)>0})
    result = []
    window = int(config["window"])
    catalogue = _catalogue(scope,window,config["weighting"])
    configured_years = config.get("years")
    custom=config.get("custom")
    lags = [asof.year-int(year) for year in configured_years] if configured_years is not None else None
    for identity,group in candidates.groupby("identity",sort=False):
        first = group.iloc[0]
        prices = group.sort_values("quote_day").drop_duplicates("quote_day",keep="last").set_index("quote_day").price.reindex(days)
        prices = prices.where(prices.index <= first.delivery_end)
        values = prices.to_numpy(float)
        observed = np.flatnonzero(np.isfinite(values))
        if not len(observed) or (not custom and len(observed)<window):
            continue
        weights = np.ones(window)
        if config["weighting"] == "Recent weighted":
            weights = (1-2/(window+1)) ** np.arange(window-1,-1,-1)
        levels = np.full(len(values),np.nan)
        if custom:
            for i in observed:
                shift=days[i]-asof
                a,b=[pd.Timestamp(d)+shift for d in custom]
                sample=prices.loc[a:min(b,days[i])].dropna()
                if len(sample):levels[i]=reference_values(sample,len(sample),config["weighting"])["level"]
        else:
            rolling = np.lib.stride_tricks.sliding_window_view(values[observed],window)
            levels[observed[window-1:]] = rolling @ (weights/weights.sum())
        offsets = (first.delivery_start.year-days.year)*12+first.delivery_start.month-days.month
        anchors = np.flatnonzero(np.isfinite(levels) & (offsets == offset))
        for i in anchors:
            origin = days[i]
            years = configured_years if config.get("year_mode")=="Fixed years" else ([origin.year-lag for lag in lags] if lags is not None else None)
            historical_custom=[str((pd.Timestamp(d)+(origin-asof)).date()) for d in custom] if custom else None
            analogues = _analogues(catalogue,first,origin,years,custom=historical_custom,weighting=config["weighting"])
            if lags is None:
                analogues = analogues[:5]
            if not analogues:
                continue
            valid_h = [h for h in horizon_set if i+h<len(days) and np.isfinite(values[i+h])]
            targets = pd.DatetimeIndex([days[i+h] for h in valid_h])
            predictions,_,_,numbers = _seasonal_points(targets,levels[i],analogues,config.get("model","Difference"))
            for h,predicted,number in zip(valid_h,predictions,numbers):
                if not np.isfinite(predicted):
                    continue
                result.append(dict(date=origin,target_date=days[i+h],contract=identity,delivery_start=first.delivery_start,
                                   horizon=h,actual=values[i+h],forecast=predicted,historical_n=int(number),unchanged=values[i],
                                   reference_mean=levels[i],error=values[i+h]-predicted))
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


def spot_forecast_path(fixed,config,asof=None):
    rows=fixed[fixed.kind.eq("Spot") & fixed.price.notna()].sort_values("quote_day").drop_duplicates("quote_day",keep="last")
    if asof is not None:
        rows=rows[rows.quote_day.le(asof)]
    if rows.empty:
        return pd.DataFrame(),{}
    origin=rows.quote_day.max(); meta=rows.iloc[-1]
    if config.get("year_mode")=="Rolling years" and config.get("years"):
        shift=origin.year-int(config.get("year_anchor",origin.year))
        config["years"]=[int(y)+shift for y in config["years"]]
        config["year_anchor"]=origin.year
    series=rows.set_index("quote_day").price
    custom=config.get("custom")
    sample=series.loc[pd.Timestamp(custom[0]):min(origin,pd.Timestamp(custom[1]))] if custom else series.tail(int(config["window"]))
    ref=reference_values(sample,len(sample) if custom else int(config["window"]),config["weighting"]) if len(sample) else None
    if ref is None:
        return pd.DataFrame(),dict(reason="Insufficient current reference quotes.")
    end=pd.Timestamp(config.get("forecast_end",origin+pd.DateOffset(months=3)))
    days,assumed=future_grid(origin,end)
    available=[]
    for year in sorted(series.index.year.unique(),reverse=True):
        lag=origin.year-int(year)
        if lag<=0:
            continue
        cutoff=origin-pd.DateOffset(years=lag)
        historical=series.loc[:cutoff]
        sample=historical.loc[pd.Timestamp(custom[0])-pd.DateOffset(years=lag):pd.Timestamp(custom[1])-pd.DateOffset(years=lag)] if custom else historical.tail(int(config["window"]))
        b=reference_values(sample,len(sample) if custom else int(config["window"]),config["weighting"]) if len(sample) else None
        if b is not None and (cutoff-sample.index[-1]).days<=8:
            available.append(dict(year=int(year),lag=lag,contract="Spot",series=series,reference=b["level"]))
    years=config.get("years")
    analogues=[a for a in available if a["year"] in years] if years is not None else available[:5]
    if not analogues:
        return pd.DataFrame(),dict(reason="No historical years available before this forecast date.")
    if config.get("_target_dates"):
        days=pd.DatetimeIndex(pd.to_datetime(config["_target_dates"]))
        days=days[(days>origin)&(days<=end)]
    values,c,b,n,members=_seasonal_points(days,ref["level"],analogues,config.get("model","Difference"),return_years=True)
    path=pd.DataFrame(dict(target_date=days,horizon=np.arange(1,len(days)+1),forecast=values,historical_mean=c,
                           historical_reference=b,historical_n=n,historical_years=members,unchanged=ref["latest"],reference_mean=ref["level"]))
    if path.empty or not path.forecast.notna().any():
        if config.get('model')=='Ratio' and np.any((n>0)&(b<=1e-8)):
            return pd.DataFrame(),dict(reason="Ratio requires a positive, non-zero historical reference mean. Use Difference for this selection.")
        return pd.DataFrame(),dict(reason="No common historical coverage for the selected years and range.")
    path["scenario"]=apply_judgement(path,config)
    info=dict(asof=origin,expiry=end,delivery_start=end.replace(day=1),tenor="spot",target_kind="Spot",vessel=meta.vessel,
              product=meta["product"],currency=meta.currency,unit=meta.unit,**ref,config=config,assumed_calendar=assumed,
              candidate_years=[a["year"] for a in available],reference_years=[a["year"] for a in analogues],
              historical_reference=float(np.mean([a["reference"] for a in analogues])),source_revision="" if config.get("_replay") else data_revision(rows))
    return path,info


def replay_current(fixed,contract,config,horizon,strict=False,target_date=None):
    kind=config.get("target_kind","BFA")
    if kind=="Spot":
        rows=fixed[fixed.kind.eq("Spot")].sort_values("quote_day").drop_duplicates("quote_day")
    else:
        rows=fixed[fixed.kind.eq("BFA") & fixed.identity.eq(contract)].sort_values("quote_day").drop_duplicates("quote_day")
    if rows.empty:
        return pd.DataFrame()
    if target_date is not None and pd.Timestamp(target_date) not in set(rows.quote_day):
        last=rows.quote_day.max()
        end=rows.delivery_end.iloc[-1] if kind=="BFA" else pd.Timestamp(target_date)
        if pd.Timestamp(target_date) not in future_grid(last,end,kind)[0]:
            return pd.DataFrame()
    if kind=="BFA" and config.get("pooled") and not strict and target_date is None:
        raw=validation_errors(fixed,contract,config,[horizon])
        cases=independent_errors(raw,horizon)
        if cases.empty:return cases
        cases=cases.copy();cases["anchor"]=cases.unchanged
        cases["momentum"]=np.nan;cases["majority"]=np.nan
        observed_calendar=pd.DatetimeIndex(sorted(fixed.loc[fixed.kind.eq("BFA"),"quote_day"].unique()))
        grouped={identity:g.sort_values("quote_day").drop_duplicates("quote_day").set_index("quote_day").price for identity,g in fixed[fixed.kind.eq("BFA")].groupby("identity")}
        for idx,row in cases.iterrows():
            past=grouped[row.contract].reindex(observed_calendar).loc[:row.date]
            changes=past.diff()
            if len(changes) and np.isfinite(changes.iloc[-1]):
                cases.loc[idx,"momentum"]=row.anchor+changes.iloc[-1]*horizon
            directions=np.sign(past-past.shift(horizon)).dropna()
            if len(directions):cases.loc[idx,"majority"]=directions.mode().iloc[0]
        return cases
    calendar=pd.DatetimeIndex(sorted(fixed.loc[fixed.kind.eq(kind),"quote_day"].unique()))
    latest=rows.quote_day.max(); years=config.get("years")
    offsets=[latest.year-int(y) for y in years] if years is not None else None
    actual=rows.set_index("quote_day").price
    origins=rows.quote_day.iloc[-1500:] if target_date is None else rows.loc[rows.quote_day.lt(pd.Timestamp(target_date)),"quote_day"].iloc[-120:]
    # Strict replay must build its catalogue from the timestamp-filtered snapshot.
    prepared=_catalogue(fixed,int(config["window"]),config["weighting"]) if kind=="BFA" and not strict else None
    # Non-overlapping test targets, never fit parameters to these outcomes.
    output=[]; next_origin=pd.Timestamp.min
    for origin in origins:
        if target_date is None and origin<next_origin:
            continue
        i=calendar.searchsorted(origin)
        if target_date is None:
            if i+horizon>=len(calendar):continue
            target=calendar[i+horizon]
            if target not in actual.index:continue
        else:
            target=pd.Timestamp(target_date)
        known=fixed[fixed.quote_day.le(origin)]
        if strict:
            if "available_at" not in known:
                continue
            stamps=pd.to_datetime(known.available_at,utc=True,errors="coerce")
            known=known[stamps.notna() & stamps.le(origin.tz_localize("UTC")+pd.Timedelta(days=1)-pd.Timedelta(nanoseconds=1))]
        available=known[known.kind.eq(kind)]
        if kind=="BFA":available=available[available.identity.eq(contract)]
        if available.empty or available.quote_day.max()!=origin:continue
        cfg={**config,"forecast_end":str(target.date()),"judgement":"Neutral","_replay":True,"_target_dates":[str(target.date())]}
        if offsets is not None:
            cfg["years"]=list(years) if config.get("year_mode")=="Fixed years" else [origin.year-lag for lag in offsets]
        cfg["year_anchor"]=origin.year
        if config.get("custom"):
            shift=origin-latest
            cfg["custom"]=[str((pd.Timestamp(d)+shift).date()) for d in config["custom"]]
        path,info=forecast_path(known,contract,cfg,asof=origin,_prepared=prepared)
        if path.empty:
            continue
        point=path[path.target_date.eq(target)].dropna(subset=["forecast"])
        if point.empty:
            continue
        prediction=float(point.forecast.iloc[0]); anchor=float(actual.loc[origin])
        past=actual.reindex(calendar).loc[:origin]
        changes=past.diff()
        directions=np.sign(past-past.shift(horizon)).dropna()
        majority=float(directions.mode().iloc[0]) if len(directions) and target_date is None else np.nan
        momentum=anchor+float(changes.iloc[-1])*horizon if target_date is None and np.isfinite(changes.iloc[-1]) else np.nan
        output.append(dict(date=origin,target_date=target,forecast=prediction,actual=float(actual.get(target,np.nan)),
                           anchor=anchor,unchanged=anchor,momentum=momentum,majority=majority,historical_n=int(point.historical_n.iloc[0])))
        next_origin=target
    return pd.DataFrame(output)


def price_direction(value,anchor):
    difference=np.asarray(value)-np.asarray(anchor)
    return np.sign(np.where(np.abs(difference)<=1e-8,0.,difference))


def forecast_score(rows,min_samples=10):
    if not rows.empty:
        rows=rows.loc[np.isfinite(rows[["forecast","actual","anchor"]]).all(axis=1)]
    if rows.empty or len(rows)<min_samples:
        return dict(samples=len(rows),mae=np.nan,skill=np.nan,direction=np.nan)
    error=rows.forecast-rows.actual; baseline=(rows.anchor-rows.actual).abs().mean()
    return dict(samples=len(rows),mae=float(error.abs().mean()),skill=float(100*(1-error.abs().mean()/baseline)) if baseline>1e-8 else np.nan,
                direction=float((price_direction(rows.forecast,rows.anchor)==price_direction(rows.actual,rows.anchor)).mean()),
                r2=float(1-np.square(error).sum()/np.square(rows.actual-rows.actual.mean()).sum()) if np.square(rows.actual-rows.actual.mean()).sum()>1e-8 else np.nan,
                bias=float(error.mean()),momentum_mae=float((rows.momentum-rows.actual).abs().mean()),
                majority_accuracy=float((rows.loc[rows.majority.notna(),"majority"]==price_direction(rows.loc[rows.majority.notna(),"actual"],rows.loc[rows.majority.notna(),"anchor"])).mean()))
