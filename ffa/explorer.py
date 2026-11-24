"""One chart for outright prices, seasonal overlays and paired comparisons."""
import pandas as pd
import streamlit as st

from .analytics import monthly_spot, options, period_means, seasonal, timeline, unit_label
from .preferences import choice, multi, toggle
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
    if overlay and mode != "Absolute":
        st.info("Choose Actual Date to calculate matched-date comparisons. Calendar Overlay shows original prices.")
        return
    frequencies = list(FREQUENCIES)
    frequency = choice("Frequency", frequencies, key + "_freq", control="segmented_control")
    with st.popover("Advanced Settings"):
        wide = toggle("Include other benchmarks", key + "_wide")
        kinds = multi("Series types", ["Spot", "Spot monthly mean", "Fixed", "Rolling"], key + "_kinds",
                      ["Spot", "Spot monthly mean", "Fixed", "Rolling"], 4)
        slider = toggle("Show range slider", key + "_slider")
    if overlay:
        years = list(range(last.year, first.year - 1, -1))
        years = sorted(multi("Overlay years", years, key + "_years", years[:5], len(years)), reverse=True)
        if not years:
            return
        start = max(pd.Timestamp(first), pd.Timestamp(min(years), 1, 1))
        end = min(pd.Timestamp(last), pd.Timestamp(max(years), 12, 31))
        bounds = start, end
    view = load(*(item.date() for item in bounds), wide=wide)
    series = options(view.fixed, view.rolling) if wide else view.local
    candidates = {leg: item for leg, item in series.items()
                  if (leg[2] if leg[2] == "Spot" else leg[6]) in kinds
                  and item[1].quote_day.between(*bounds).any()}
    labels = {leg: f"{series_name(leg, series, full=wide)} · {'Spot' if leg[2] == 'Spot' else leg[6]} · {unit_label(*leg[3:5])}"
              for leg in candidates}
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
    selected = multi("Series", sorted(candidates, key=labels.get), key + "_series_" + axis,
                     preferred or list(candidates)[:1], 8, format_func=labels.get)
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
        parts = list(raw.groupby(raw.index.year)) if overlay else [(None, raw)]
        for year, part in parts:
            if overlay and year not in years:
                continue
            rolls = part.loc[part.roll_from.notna() & part.price.notna()].copy()
            data = _aggregate(part, frequency, bounds)
            if data.empty:
                continue
            data["Observed"] = data.index.strftime("%d %b %Y")
            label = f"{name} · {year}" if overlay else name
            if overlay:
                data = seasonal(data).set_index("season_day")
                rolls = seasonal(rolls).set_index("season_day")
            # Mark rolls using the actual daily comparison value even in an aggregated view.
            draw_segments(fig, data, "price", label, trace_index, "%{customdata[0]}<br><b>%{y:,.3~f}</b>",
                          ["Observed"], level=frequency == "Daily" and not overlay)
            if overlay or frequency != "Daily":
                mark_rolls(fig, rolls, name=label, index=trace_index, seasonal_axis=overlay)
            trace_index += 1
    if not fig.data:
        st.info("There are no valid aligned observations for these selections.")
        return
    if overlay:
        date_axis(fig, (pd.Timestamp("2000-01-01"), pd.Timestamp("2000-12-31")))
        fig.update_xaxes(tickformat="%b", dtick="M1")
    else:
        date_axis(fig, bounds)
    fig.update_xaxes(rangeslider_visible=slider)
    fig.update_layout(uirevision=str((bounds, selected, mode, axis, frequency)))
    display(fig, key + "_main")
    if origin is not None:
        st.caption(f"Common base date {origin:%d %b %Y}. Only dates observed for every selected series are shown.")
    st.caption("Period means use available assessments within each delivery segment. Diamonds mark original roll dates.")
