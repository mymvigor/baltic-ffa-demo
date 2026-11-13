import re

import pandas as pd
import streamlit as st

from .analytics import compare, delivery_label, indicators, timeline, unit_label
from .preferences import choice, multi, toggle
from .spot import monthly_overlay
from .ui import COLORS, VALUE_HOVER, average_history, bollinger_history, chart, date_axis, display, draw_segments, rolling_leg, section, series_name


def prepared(rows, days):
    data = timeline(rows, days)
    data["Delivery"] = [delivery_label(t, d) if pd.notna(t) else "—"
                        for t, d in zip(data.tenor, data.delivery_start)]
    return data


def draw_history(fig, data, column, name, index):
    draw_segments(fig, data, column, name, index, VALUE_HOVER, level=column == "price")


def paired(a, b, divide=False, align=False):
    return compare(a, b, divide=divide, align=align, window=20)


def show_pair(fig, data, column, name, index):
    draw_segments(fig, data, column, name, index, VALUE_HOVER, level=True)


def show(fig, bounds, key, empty_message, unified=False):
    if fig.data:
        date_axis(fig, bounds)
        if unified:
            fig.update_layout(hovermode="x unified")
            fig.update_layout(hoverlabel_font_family="Consolas, monospace")
        display(fig, key)
    else:
        st.info(empty_message)


def same_day_hover(fig, forward, spot, forward_name):
    prices = pd.concat([forward.price, spot.price], axis=1).dropna(how="all")
    if prices.empty:
        return

    fig.update_traces(hoverinfo="skip", hovertemplate=None)
    # Identical x coordinates keep both hover rows on the same assessment date.
    anchor = prices.iloc[:, 0].combine_first(prices.iloc[:, 1])
    names = (forward_name, "Spot")
    width = max(map(len, names))
    for index, name in enumerate(names):
        values = prices.iloc[:, index]
        label = name.ljust(width + 3).replace(" ", "&nbsp;")
        labels = []
        for value in values:
            formatted = f"{value:,.3f}".rstrip("0").rstrip(".") if pd.notna(value) else "No quote"
            number = formatted.rjust(12).replace(" ", "&nbsp;")
            labels.append(f"{label}<b>{number}</b>")
        fig.add_scatter(
            x=prices.index, y=values.fillna(anchor), name=name, legendgroup=name,
            mode="markers", marker=dict(size=0.01, color=COLORS[index]), showlegend=False,
            text=labels, hovertemplate="%{text}<extra></extra>",
        )


def spot_comparison(local, days, bounds, key):
    choices = [leg for leg in local if leg[2] == "BFA"]
    if not choices:
        st.info("No BFA series available.")
        return {}
    default = rolling_leg(local, "+1MON") or choices[0]
    left, right = st.columns([2, 1], vertical_alignment="bottom")
    leg = choice("BFA contract", choices, key + "_basis_leg", default, container=left,
                 format_func=lambda leg: series_name(leg, local))
    unit = leg[3:5]
    spot = next((item for item in local if item[2] == "Spot" and item[3:5] == unit), None)
    show_spot = toggle("Show Spot", key + "_show_spot", default=True, container=right) if spot else False
    show_monthly = toggle("Spot monthly mean", key + "_spot_monthly", default=True) if spot else False
    show_basis = False
    measure = "Price difference"
    if show_spot:
        with st.popover("Basis options"):
            show_basis = toggle("Show basis chart", key + "_show_basis", default=True)
            if show_basis:
                measure = choice("Basis display", ["Price difference", "Percent"], key + "_basis_measure")

    window = slice(*bounds)
    forward = prepared(local[leg][1], days)
    name = series_name(leg, local)
    forward_name = "BFA · " + name
    price_fig = chart(unit_label(*unit))
    draw_history(price_fig, forward.loc[window], "price", forward_name, 0)
    average_groups = {"Prices": [(forward_name, forward, "price")]}
    average_units = {"Prices": unit_label(*unit)}
    basis = {}
    if spot:
        spot_data = prepared(local[spot][1], days)
        result = paired(forward, spot_data)
        basis[f"{name} − Spot"] = (unit, result)
        if show_spot:
            draw_history(price_fig, spot_data.loc[window], "price", "Spot", 1)
            same_day_hover(price_fig, forward.loc[window], spot_data.loc[window], forward_name)
            average_groups["Prices"].append(("Spot", spot_data, "price"))
            average_groups["Basis"] = [("BFA − Spot", result, "price")]
            average_units["Basis"] = unit_label(*unit)
    if show_monthly:
        monthly_overlay(price_fig, local[spot][1], bounds)
        st.caption("Spot monthly mean uses stored daily assessments, plotted on the last observed day of each month. The latest month is month-to-date; missing months stay empty.")
    show(price_fig, bounds, key + "_spot_overlay", "No spot or forward quotes in the selected range.",
         unified=show_spot)
    if show_basis:
        basis_fig = chart("% of spot" if measure == "Percent" else unit_label(*unit), height=235)
        percent = result.copy()
        percent["percent"] = 100 * percent.price / percent.b.where(percent.b.gt(0))
        view = percent.loc[window]
        show_pair(basis_fig, view, "percent" if measure == "Percent" else "price", "BFA − Spot", 0)
        show(basis_fig, bounds, key + "_spot_basis", "No same-day spot and BFA quotes in this range.")
        if measure == "Percent":
            average_groups["Basis"] = [("BFA − Spot (%)", percent, "percent")]
            average_units["Basis"] = "% of spot"
    bollinger_history(average_groups, bounds, average_units, key + "_spot")
    average_history(average_groups, bounds, average_units, key + "_spot")
    return basis


def calendar_spreads(local, days, bounds, key):
    st.markdown("**Calendar spread · Near − Far**")
    legs = [leg for leg in local if leg[2] == "BFA"]
    if len(legs) < 2:
        st.info("At least two forward contracts are needed.")
        return {}

    presets = {"Custom legs": None}
    for tenor, suffix, label in [
        ("month", "MON", "M+1 − M+2"),
        ("quarter", "Q", "Q+1 − Q+2"),
        ("year", "CAL", "Cal+1 − Cal+2"),
    ]:
        near = next((leg for leg in legs if leg[6] == "Rolling" and leg[5] == tenor
                     and re.search(r"\+1" + suffix + r"(?:#\d+)?$", leg[-1])), None)
        far = next((leg for leg in legs if near and leg[3:5] == near[3:5] and leg[6] == "Rolling"
                    and leg[5] == tenor and re.search(r"\+2" + suffix + r"(?:#\d+)?$", leg[-1])), None)
        if near and far:
            presets[label] = near, far

    preset = choice("Spread", presets, key + "_calendar_preset", list(presets)[int(len(presets) > 1)]) if len(presets) > 1 else "Custom legs"
    if preset == "Custom legs":
        left, right = st.columns(2)
        active = [leg for leg in legs if local[leg][1].delivery_end.max() >= bounds[1]]
        preferred = next((leg for leg in active if leg[5] == "month"), (active or legs)[0])
        near = choice("Near leg", legs, key + "_calendar_near", preferred, container=left,
                      format_func=lambda leg: series_name(leg, local))
        matches = [leg for leg in legs if leg != near and leg[3:5] == near[3:5]]
        if not matches:
            st.info("No compatible far leg.")
            return {}
        later = [leg for leg in matches if leg[5] == near[5] and local[leg][1].delivery_start.min() > local[near][1].delivery_start.min()]
        far = choice("Far leg", matches, key + "_calendar_far_" + str(near),
                     (later or matches)[0], container=right, format_func=lambda leg: series_name(leg, local))
    else:
        near, far = presets[preset]

    unit = near[3:5]
    a, b = (prepared(local[leg][1], days) for leg in (near, far))
    ordered = a.delivery_start.lt(b.delivery_start)
    result = paired(a, b)
    result["price"] = result.price.where(ordered)
    name = f"{series_name(near, local)} − {series_name(far, local)}"
    fig = chart(unit_label(*unit), height=315)
    show_pair(fig, result.loc[bounds[0]:bounds[1]], "price", name, 0)
    show(fig, bounds, key + "_calendar_chart", "No same-day quotes with confirmed near-before-far delivery in this range.")
    bollinger_history({"Spread": [(name, result, "price")]}, bounds, unit_label(*unit), key + "_calendar")
    average_history({"Spread": [(name, result, "price")]}, bounds, unit_label(*unit), key + "_calendar")
    return {name: (unit, result)}


def comparison_studies(local, days):
    near = rolling_leg(local, "+1MON")
    if near is None:
        return {}, {}
    unit = near[3:5]
    name, rows = local[near]
    a = prepared(rows, days)
    spot = next((leg for leg in local if leg[2] == "Spot" and leg[3:5] == unit), None)
    far = rolling_leg(local, "+2MON", unit)
    basis, calendar = {}, {}
    if spot:
        basis[f"{name} − Spot"] = (unit, paired(a, prepared(local[spot][1], days)))
    if far:
        b = prepared(local[far][1], days)
        spread = paired(a, b)
        spread["price"] = spread.price.where(a.delivery_start.lt(b.delivery_start))
        calendar[f"{name} − {local[far][0]}"] = (unit, spread)
    return basis, calendar


def comparison_workspace(series, local, basis, calendar, bounds, key, pair_mode):
    scope, method_column = st.columns(2)
    universe = choice("Universe", ["Current benchmark", "All benchmarks"], key + "_compare_universe", container=scope)
    methods = ["Spread", "Ratio"] if pair_mode else ["Overlay", "Rebased to 100"]
    method = choice("View", methods, key + "_compare_view", container=method_column,
                    control="radio", horizontal=True)
    metrics = {"Price or level": "price", "1-assessment change (%)": "change",
               "5-assessment mean": "ma5", "20-assessment mean": "ma20"}
    metric = "Price or level"
    if method == "Overlay":
        with st.popover("Series options"):
            metric = choice("Measure", metrics, key + "_compare_metric")
    available = local if universe == "Current benchmark" else series
    derived = {} if method == "Rebased to 100" or metric != "Price or level" else {**basis, **calendar}
    if not available:
        st.info("No comparison series are available.")
        return
    preferred = rolling_leg(local, "+1MON") or next(iter(available))
    units = list(dict.fromkeys([preferred[3:5], *(leg[3:5] for leg in available)]))
    enforce_unit = method != "Rebased to 100" and metric != "1-assessment change (%)"
    unit = choice("Quote unit", units, key + "_compare_unit", format_func=lambda pair: unit_label(*pair)) if enforce_unit and len(units) > 1 else units[0]
    candidates = [leg for leg in available if not enforce_unit or leg[3:5] == unit]
    studies = [name for name, (measure_unit, _) in derived.items() if not enforce_unit or measure_unit == unit]
    spot = next((leg for leg in candidates if leg in local and leg[2] == "Spot"), None)
    defaults = [preferred] if preferred in candidates else candidates[:1]
    second = rolling_leg(local, "+2MON", unit)
    alternative = second if second in candidates and second not in defaults else spot
    if alternative and alternative not in defaults:
        defaults.append(alternative)

    def name(item):
        if isinstance(item, str):
            return item
        label = series_name(item, available, full=True)
        return label if item[2] == "Spot" else f"{label} · {item[6]}"

    pair = pair_mode
    eligible = [*candidates, *studies]
    series_key = key + f"_compare_legs_{universe}_{metric}_{unit}"
    if not eligible or (pair and len(eligible) < 2):
        st.info("Not enough compatible series for this view.")
        return
    if pair:
        left, right = st.columns(2)
        first = choice("Leg A", eligible, series_key + "_a", defaults[0] if defaults else None,
                       container=left, format_func=name)
        second = choice("Leg B", [item for item in eligible if item != first], series_key + "_b",
                        defaults[1] if len(defaults) > 1 else None, container=right, format_func=name)
        selected = [first, second]
    else:
        selected = multi("Series", eligible, series_key + method, defaults, 5, format_func=name)
    if not selected or (pair and len(selected) != 2):
        st.info("Select two series for this comparison." if pair else "Select one or more series.")
        return

    dates = {}
    for item in selected:
        if isinstance(item, str):
            dates[item] = derived[item][1].index
        else:
            dates[item] = pd.DatetimeIndex(sorted(
                {day for leg, (_, rows) in series.items() if leg[:2] == item[:2] and leg[3:5] == item[3:5]
                 for day in rows.quote_day}
            ))
    window = slice(*bounds)
    average_sources = []
    if pair:
        a, b = selected
        align = not isinstance(a, str) and not isinstance(b, str) and a[0] != b[0] and a[2] == b[2] == "BFA"
        common_days = dates[a].union(dates[b])
        panels = []
        for item in selected:
            if not isinstance(item, str):
                panels.append(prepared(available[item][1], common_days))
                continue
            data = derived[item][1].reindex(common_days).copy()
            data["segment"] = data.segment.ffill().fillna(0)
            data["tenor"] = "study"
            data["delivery_start"] = pd.NaT
            data["delivery_end"] = pd.NaT
            panels.append(data)
        history = paired(*panels, divide=method == "Ratio", align=align)
        result = history.loc[window]
        fig = chart("Ratio" if method == "Ratio" else unit_label(*unit))
        show_pair(fig, result, "price", f"{name(a)} {'÷' if method == 'Ratio' else '−'} {name(b)}", 0)
        average_sources.append((f"{name(a)} {'÷' if method == 'Ratio' else '−'} {name(b)}", history, "price"))
        average_unit = "Ratio" if method == "Ratio" else unit_label(*unit)
        if align:
            st.caption("Cross-vessel BFA legs are matched by date and actual delivery period.")
    else:
        column = metrics[metric]
        label = "Index (100 = base)" if method == "Rebased to 100" else "%" if column == "change" else unit_label(*unit)
        fig = chart(label)
        average_unit = label
        raw = {item: prepared(available[item][1], dates[item]) for item in selected if not isinstance(item, str)}
        base_day = None
        if method == "Rebased to 100":
            overlap = pd.concat([data.loc[window].price for data in raw.values()], axis=1)
            common = overlap.gt(0).all(axis=1)
            if not common.any():
                st.info("Rebasing needs a common date with positive prices for every selected series.")
                return
            base_day = common[common].index[0]
            st.caption(f"Base date · {base_day:%d %b %Y}")
        for index, item in enumerate(selected):
            if isinstance(item, str):
                show_pair(fig, derived[item][1].loc[window], "price", name(item), index)
                average_sources.append((name(item), derived[item][1], "price"))
                continue
            data = raw[item]
            values = indicators(data).copy() if column != "price" else data.copy()
            if column == "change":
                values[column] *= 100
            if base_day is not None:
                values = values.loc[base_day:].copy()
                values[column] = 100 * values[column] / data.loc[base_day, "price"]
            draw_history(fig, values.loc[window], column, name(item), index)
            average_sources.append((name(item), values, column))

    show(fig, bounds, key + "_workspace", "No matched prices or sufficient history for the selected view.")
    bollinger_history({"Selected series": average_sources}, bounds, average_unit, key + "_workspace")
    average_history({"Selected series": average_sources}, bounds, average_unit, key + "_workspace")


def historical_context(local, days, basis, calendar, bounds, key):
    st.caption("Historical context")
    spot = next((leg for leg in local if leg[2] == "Spot"), None)
    front = rolling_leg(local, "+1MON", spot[3:5]) if spot else None
    if front:
        with st.container():
            st.markdown("**Spot and M+1 correlation**")
            spot_return, front_return = (indicators(prepared(local[leg][1], days)).change for leg in (spot, front))
            correlation = spot_return.rolling(20, min_periods=20).corr(front_return).clip(-1, 1).loc[bounds[0]:bounds[1]]
            if correlation.notna().any():
                fig = chart("Correlation", height=260)
                fig.add_scatter(x=correlation.index, y=correlation, mode="lines", connectgaps=False,
                                name="20-assessment correlation",
                                hovertemplate="<b>%{fullData.name}</b><br>%{x|%d %b %Y}<br><b>%{y:.2f}</b><extra></extra>")
                fig.update_yaxes(range=[-1, 1])
                date_axis(fig, bounds)
                display(fig, key + "_correlation")
            else:
                st.info("Correlation needs 20 consecutive matched changes with variation in both series.")

    with st.container():
        st.markdown("**Rolling percentiles and volatility**")
        studies = {**basis, **calendar}
        if not studies:
            st.info("Select a spot basis or calendar spread first.")
            return
        name = choice("Study", studies, key + "_historical_study")
        unit, data = studies[name]
        data = data.copy()
        samples = data.groupby("segment").price
        for column, probability in [("Lower", .10), ("Median", .50), ("Upper", .90)]:
            data[column] = samples.transform(lambda values: values.rolling(20, min_periods=20).quantile(probability))
        data["Volatility"] = samples.transform(lambda values: values.diff().rolling(20, min_periods=20).std())
        visible = data.loc[bounds[0]:bounds[1]]
        if not visible.Median.notna().any():
            st.info("Percentiles need 20 consecutive matched observations in a roll segment.")
            return
        fig = chart(unit_label(*unit), height=300)
        for index, (column, label) in enumerate([("price", name), ("Median", "Median"), ("Lower", "10th percentile"),
                                                 ("Upper", "90th percentile")]):
            draw_segments(fig, visible, column, label, index, VALUE_HOVER)
        date_axis(fig, bounds)
        display(fig, key + "_percentiles")
        if visible.Volatility.notna().any():
            vol = chart(unit_label(*unit), height=260)
            draw_segments(vol, visible, "Volatility", "20-assessment change volatility", 0, VALUE_HOVER)
            date_axis(vol, bounds)
            display(vol, key + "_volatility")
