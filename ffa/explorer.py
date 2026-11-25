"""One chart for outright prices, seasonal overlays and paired comparisons."""
import pandas as pd
import streamlit as st

from .analytics import monthly_spot, options, period_means, seasonal, timeline, unit_label
from .preferences import choice, multi, toggle
from .selection import contract_label
from .ui import FREQUENCIES, chart, date_axis, display, draw_segments, mark_rolls, rolling_leg, series_name


def compare_prices(series, mode, base):
    """Align exact assessment dates before deriving prices or rebasing."""
    dates = series[0].index
    for item in series[1:]:
        dates = dates.union(item.index)
    raw = pd.concat([item.price.reindex(dates) for item in series], axis=1, ignore_index=True)
    common = raw.dropna()
    if mode == "Normalized":
        common = common.loc[common.ne(0).all(axis=1)]
        if common.empty:
            return [], None
        start = common.index[0]
    else:
        start = None
    output = []
    for index, item in enumerate(series):
        if mode in ("Difference", "Ratio") and index == base:
            continue
        result = item.reindex(dates).copy()
        if mode == "Normalized":
            result["price"] = (100 * raw[index] / raw.loc[start, index]).where(raw.notna().all(axis=1))
            result = result.loc[start:]
        elif mode in ("Difference", "Ratio"):
            result["price"] = (raw[index] - raw[base] if mode == "Difference" else
                               raw[index] / raw[base].where(raw[base].abs().gt(1e-12)))
            # Either leg's delivery change breaks a paired series.
            reference = series[base].reindex(dates)
            if "Observed" in reference:
                result["Reference observed"] = reference.Observed
            segments = pd.concat([result.segment.ffill(), reference.segment.ffill()], axis=1)
            result["segment"] = segments.ne(segments.shift()).any(axis=1).cumsum()
            result["kind"] = "Derived"
            roll = reference.roll_from.notna() & result.roll_from.isna()
            result.loc[roll, ["roll_from", "roll_to"]] = reference.loc[roll, ["roll_from", "roll_to"]]
        output.append((index, result))
    return output, start


def _monthly(data, bounds):
    means = period_means(data, "price", "M", bounds)
    if means.empty:
        return means.set_index("date")
    result = means.set_index("date")
    months = pd.Series(result.index.to_period("M").asi8, index=result.index)
    result["segment"] = months.diff().gt(1).cumsum()
    return result


def _aggregate(data, frequency, bounds):
    if frequency == "Daily":
        return data
    result = period_means(data, "price", FREQUENCIES[frequency], bounds).set_index("date")
    if not result.empty:
        periods = result.index.to_period(FREQUENCIES[frequency]).asi8
        gaps = pd.Series(periods, index=result.index).diff().gt(1)
        result["segment"] = (result.segment.ne(result.segment.shift()) | gaps).cumsum()
    return result


def explorer(load, bounds, first, last, key):
    axis = choice("Time Axis", ["Actual Date", "Calendar Overlay"], key + "_axis", control="segmented_control")
    mode = choice("Compare Mode", ["Absolute", "Normalized", "Difference", "Ratio"], key + "_mode",
                  control="segmented_control")
    overlay = axis == "Calendar Overlay"
    frequencies = list(FREQUENCIES)
    frequency = choice("Frequency", frequencies, key + "_freq", control="segmented_control")
    with st.popover("Advanced Settings"):
        wide = toggle("Include other benchmarks", key + "_wide")
        kinds = multi("Series types", ["Spot", "Spot monthly mean", "Fixed", "Rolling"], key + "_kinds",
                      ["Spot", "Spot monthly mean", "Fixed", "Rolling"], 4)

    # Load the inventory independently of display range, so old contracts stay selectable.
    bounds=pd.Timestamp(first),pd.Timestamp(last)
    view = load(*(item.date() for item in bounds), wide=wide)
    series = options(view.fixed, view.rolling) if wide else view.local
    candidates = {leg: item for leg, item in series.items()
                  if (leg[2] if leg[2] == "Spot" else leg[6]) in kinds
                  and item[1].quote_day.between(*bounds).any()}
    labels={leg:(contract_label(item[1].iloc[0])+f" · {leg[1]}" if leg[6]=="Fixed" and leg[2]=="BFA" else f"{leg[2] if leg[2]=='Spot' else 'Rolling'} · {item[0]} · {leg[1]}")+f" · {unit_label(*leg[3:5])}" for leg,item in candidates.items()}
    for leg,item in list(candidates.items()):
        if leg[2]=="Spot" or leg[6]=="Rolling":
            for year,part in item[1].groupby(item[1].quote_day.dt.year):
                alias=(*leg,"year",str(year))
                candidates[alias]=(item[0],part)
                labels[alias]=f"{leg[2] if leg[2]=='Spot' else 'Rolling'}{year} · {item[0]} · {leg[1]} · {unit_label(*leg[3:5])}"
    if "Spot monthly mean" in kinds:
        for leg, item in series.items():
            if leg[2] == "Spot" and item[1].quote_day.between(*bounds).any():
                alias = (*leg, "monthly")
                candidates[alias] = item
                labels[alias] = f"{leg[1]} · Spot monthly mean · {unit_label(*leg[3:5])}"
    if not candidates:
        st.info("No eligible series in this window.")
        return
    preferred = [leg for leg in candidates if leg[2] == "Spot" and len(leg) == 8][:1]
    near = rolling_leg(series, "+1MON", preferred[0][3:5] if preferred else None)
    if near in candidates:
        preferred.append(near)
    def priority(leg):
        row=candidates[leg][1].iloc[-1]
        if leg[2]=="Spot":return (0,len(leg)!=8,-int(leg[-1]) if len(leg)==10 else 0,labels[leg])
        if leg[6]=="Rolling":return (1,len(leg)!=8,0,labels[leg])
        return (2,row.delivery_end<pd.Timestamp(last),abs((row.delivery_start-pd.Timestamp(last)).days),labels[leg])
    selected = multi("Series", sorted(candidates, key=priority), key + "_series",
                     preferred or list(candidates)[:1], len(candidates), format_func=labels.get)
    if not selected:
        st.caption("Add a series to the chart.")
        return
    units = {leg[3:5] for leg in selected}
    if len(units) != 1:
        st.info("Choose series with the same quote unit.")
        return
    if mode != "Absolute" and len(selected) < 2:
        st.info("Add at least two series for comparison.")
        return
    base = choice("Base series", selected, key + "_base", selected[0], format_func=labels.get) if mode in ("Difference", "Ratio") else selected[0]
    sources = []
    sessions = view.fixed.groupby(["vessel", "product"]).quote_day.unique()
    for leg in selected:
        data = timeline(candidates[leg][1], pd.DatetimeIndex(sorted(sessions.loc[leg[:2]]))).loc[bounds[0]:bounds[1]]
        if len(leg) == 9:
            data = _monthly(data, bounds)
            data["kind"] = "Spot"
            data["roll_from"] = data["roll_to"] = None
        original=candidates[leg][1]
        data=data.loc[original.quote_day.min():original.quote_day.max()]
        data["Observed"]=data.index.strftime("%d %b %Y")
        if overlay and mode!="Absolute":
            if leg[2]=="BFA" and leg[6]=="Fixed":anchor=original.delivery_start.iloc[0].year
            elif len(leg)==10:anchor=int(leg[-1])
            else:
                st.info("For overlay calculations, select year-specific Spot or Rolling entries (e.g. Spot2025), or fixed contracts.")
                return
            original_dates=data.index
            mapped=original_dates+pd.DateOffset(years=2000-anchor)
            leap=(original_dates.month==2)&(original_dates.day==29)&~mapped.is_leap_year
            data.index=mapped+pd.to_timedelta(leap.astype(int)*12,unit="h")
        sources.append(data)
    results, origin = compare_prices(sources, mode, selected.index(base)) if mode != "Absolute" else (list(enumerate(sources)), None)
    y = {"Normalized": "Index · base 100", "Ratio": "Ratio"}.get(mode, unit_label(*selected[0][3:5]))
    fig = chart(y, height=390)
    trace_index = 0
    for index, raw in results:
        leg = selected[index]
        name = labels[leg]
        if mode in ("Difference", "Ratio"):
            name = f"{name} {'−' if mode == 'Difference' else '÷'} {labels[base]}"
        fixed_overlay=overlay and leg[2]=="BFA" and leg[6]=="Fixed"
        mapped_comparison=overlay and mode!="Absolute"
        parts=[(None,raw)] if not overlay or fixed_overlay or mapped_comparison else list(raw.groupby(raw.index.year))
        if overlay and len(parts)>5:parts=parts[-5:]
        for year, part in parts:
            rolls = part.loc[part.roll_from.notna() & part.price.notna()].copy()
            aggregate_bounds=(part.index.min(),part.index.max()) if mapped_comparison else bounds
            data = _aggregate(part, frequency, aggregate_bounds)
            if data.empty:
                continue
            data["Observed"]=part.Observed.reindex(data.index).fillna("") if "Observed" in part else data.index.strftime("%d %b %Y")
            label = f"{name} · {year}" if overlay and year is not None and len(leg)!=10 else name
            if mapped_comparison:
                pass
            elif fixed_overlay:
                delivery_year=candidates[leg][1].delivery_start.iloc[0].year
                shift=pd.DateOffset(years=2000-delivery_year)
                original_dates=pd.DatetimeIndex(data.index)
                mapped=original_dates+shift
                leap=(original_dates.month==2)&(original_dates.day==29)&~mapped.is_leap_year
                data.index=mapped+pd.to_timedelta(leap.astype(int)*12,unit="h")
                rolls.index=pd.DatetimeIndex(rolls.index)+shift
            elif overlay:
                data=seasonal(data).set_index("season_day")
                rolls=seasonal(rolls).set_index("season_day")
            # Mark rolls using the actual daily comparison value even in an aggregated view.
            fields=["Observed"]
            hover="%{customdata[0]}<br><b>%{y:,.3~f}</b>"
            if "Reference observed" in part:
                data["Reference observed"]=part["Reference observed"].reindex(data.index).fillna("")
                fields.append("Reference observed")
                hover="Series: %{customdata[0]}<br>Base: %{customdata[1]}<br><b>%{y:,.3~f}</b>"
            draw_segments(fig, data, "price", label, trace_index, hover,
                          fields, level=frequency == "Daily" and not overlay)
            if overlay or frequency != "Daily":
                mark_rolls(fig, rolls, name=label, index=trace_index, seasonal_axis=overlay)
            trace_index += 1
    if not fig.data:
        st.info("There are no valid aligned observations for these selections.")
        return
    if overlay:
        observed_x=[pd.Timestamp(x) for trace in fig.data for x in trace.x if x is not None]
        lo,hi=min(observed_x),max(observed_x)
        date_axis(fig,(lo,hi))
        ticks=pd.date_range(lo.replace(day=1),hi+pd.offsets.MonthBegin(1),freq="MS")
        fig.update_xaxes(tickvals=ticks,ticktext=[f"{d:%b} · Y{d.year-2000:+d}" for d in ticks],title="Calendar overlay · Y+0 is the delivery year (Spot: quote year)")
    else:
        actual_x=[pd.Timestamp(x) for trace in fig.data for x in trace.x if x is not None]
        date_axis(fig,(min(actual_x),max(actual_x)))

    display(fig, key + "_main")
    if origin is not None:
        st.caption(f"Common base date {origin:%d %b %Y}. Only dates observed for every selected series are shown.")
    st.caption("Period means use available assessments within each delivery segment. Diamonds mark original roll dates.")
