import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .analytics import compare, delivery_label, indicators, options, snapshots, timeline, unit_label
from .model import VESSELS

COLORS = ["#177b81", "#3a59a6", "#b67132", "#784ea4", "#52845b", "#a74f6a"]
DASHES = ["solid", "dash", "dot", "dashdot"]
TENORS = {"month": "Monthly", "quarter": "Quarterly", "year": "Calendar year"}
WINDOWS = {"All dates": None, "Last 20 assessments": 20, "Last 60 assessments": 60, "Last 120 assessments": 120}
VALUE_HOVER = "%{x|%d %b %Y}<br>%{y:,.3~f}"


def section(number, title, description):
    st.markdown(
        f'<div class="ffa-section"><h2><span class="ffa-number">{number:02d}</span>{title}</h2>'
        f'<p>{description}</p></div>', unsafe_allow_html=True,
    )


def chart(y, height=360):
    fig = go.Figure()
    fig.update_layout(
        template="plotly_white", height=height, margin=dict(l=12, r=18, t=24, b=16),
        paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font=dict(family="Arial, sans-serif", color="#243b52", size=12),
        hovermode="closest", hoverlabel=dict(bgcolor="#172c42", font_color="#ffffff"),
        legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom", font_size=11),
        yaxis=dict(title=y, tickformat=",.3~f", gridcolor="#e7edf3", zerolinecolor="#aebfcd", automargin=True),
        xaxis=dict(gridcolor="#f0f4f8", automargin=True),
    )
    return fig


def display(fig, key):
    st.plotly_chart(
        fig, theme=None, width="stretch", key=key,
        config={"displaylogo": False, "responsive": True, "modeBarButtonsToRemove": ["toImage", "lasso2d", "select2d"]},
    )


def history_bounds(days, key):
    if len(days) == 1:
        return days[0], days[0]
    choices = [*WINDOWS, "Custom dates"]
    selected = st.selectbox("History", choices, index=2 if len(days) > 60 else 0, key=key)
    if selected == "Custom dates":
        bounds = st.date_input("Assessment date range", (days[0].date(), days[-1].date()),
                               min_value=days[0].date(), max_value=days[-1].date(), key=key + "_custom")
        if len(bounds) != 2:
            return None
        return pd.Timestamp(bounds[0]), pd.Timestamp(bounds[1])
    count = WINDOWS[selected] or len(days)
    return days[max(0, len(days) - count)], days[-1]


def series_name(key, series, full=False):
    name = series[key][0]
    if full:
        return f"{VESSELS[key[0]]} · {key[1]} · {name}"
    return name


def draw_segments(fig, data, column, name, index, hover, custom=None, dash=None):
    shown = False
    for _, part in data.groupby("segment", sort=False):
        if not part[column].notna().any():
            continue
        fig.add_scatter(
            x=part.index, y=part[column], name=name, mode="lines+markers", connectgaps=False,
            line=dict(color=COLORS[index % len(COLORS)], width=2.3, dash=dash or DASHES[index % len(DASHES)]),
            marker=dict(size=5), legendgroup=name, showlegend=not shown,
            customdata=part[custom] if custom else None, hovertemplate=hover + "<extra>%{fullData.name}</extra>",
        )
        shown = True


def mark_rolls(fig, data, fields):
    observed = data.dropna(subset=["price"])
    if observed.empty:
        return
    changes = observed[fields].ne(observed[fields].shift()).any(axis=1)
    known = ~observed[fields].isin(["Unconfirmed delivery", "—"]).any(axis=1)
    changes &= known & known.shift(fill_value=False)
    for x in observed.index[changes]:
        i = observed.index.get_loc(x)
        before, after = observed.iloc[i - 1], observed.iloc[i]
        details = " · ".join(f"{before[field]} → {after[field]}" for field in fields if before[field] != after[field])
        fig.add_scatter(
            x=[x], y=[after.price], mode="markers", marker=dict(size=9, symbol="diamond-open", color="#536e88"),
            name="Roll", text=[details], hovertemplate="%{x|%d %b %Y}<br>%{text}<extra>Roll</extra>", showlegend=False,
        )


def rolling_leg(local, suffix, unit=None):
    return next((
        key for key in local if key[6] == "Rolling" and key[5] == "month"
        and re.search(re.escape(suffix) + r"(?:#\d+)?$", key[-1])
        and (unit is None or key[3:5] == unit)
    ), None)


def prices(local, days, key):
    section(2, "Benchmark history", "Track rolling or fixed contracts alongside the matching spot assessment.")
    if not local:
        st.info("No price series are available.")
        return

    left, right = st.columns([1, 2.2])
    modes = [mode for mode in ("Rolling", "Fixed") if any(k[6] == mode for k in local)]
    mode = left.radio("Series", modes, horizontal=True, key=key + "_mode")
    all_choices = [k for k in local if k[6] == mode]
    all_choices.sort(key=lambda k: (
        k[2] == "Spot", k[5] != "month", -local[k][1].quote_day.max().value,
        local[k][1].delivery_start.min() if k[2] == "BFA" else pd.Timestamp.max, local[k][0],
    ))
    preferred = rolling_leg(local, "+1MON") if mode == "Rolling" else None
    preferred = preferred or all_choices[0]
    units = list(dict.fromkeys(k[3:5] for k in [preferred, *all_choices]))
    unit = left.selectbox("Quote unit", units, format_func=lambda pair: unit_label(*pair),
                            key=key + mode + "_price_unit") if len(units) > 1 else units[0]
    choices = [k for k in all_choices if k[3:5] == unit]
    selected = right.multiselect("Contracts", choices, default=[preferred if preferred in choices else choices[0]],
                                 format_func=lambda k: series_name(k, local), key=key + mode + str(unit) + "_legs")
    spot = next((k for k in local if k[2] == "Spot" and k[3:5] == unit), None)
    with st.popover("Chart options"):
        bounds = history_bounds(days, key + "_price_range")
        overlays = st.multiselect("Overlays", ["5-day MA", "20-day MA", "Bollinger bands"], key=key + "_overlays")
        add_spot = st.checkbox("Show matching spot", value=True, key=key + "_spot") if spot else False
    if add_spot and spot not in selected:
        selected = [*selected, spot]
    if bounds is None:
        st.info("Select both dates to display price history.")
        return
    if not selected:
        st.info("Select a contract to display its price history.")
        return

    fig = chart(unit_label(*unit))
    for index, leg in enumerate(selected):
        name, source = local[leg]
        data = indicators(timeline(source, days)).loc[bounds[0]:bounds[1]].copy()
        data["Delivery"] = [delivery_label(t, d) if pd.notna(t) else "—" for t, d in zip(data.tenor, data.delivery_start)]
        data["Code"] = data["rolling"].fillna("—")
        hover = VALUE_HOVER + "<br>Delivery %{customdata[0]}<br>Code %{customdata[1]}"
        draw_segments(fig, data, "price", name, index, hover, ["Delivery", "Code"])
        if leg[6] == "Rolling":
            mark_rolls(fig, data, ["Delivery"])
        for label, columns in {
            "5-day MA": [("ma5", "5-day MA")],
            "20-day MA": [("ma20", "20-day MA")],
            "Bollinger bands": [("upper", "Upper band"), ("lower", "Lower band")],
        }.items():
            if label in overlays:
                for column, legend in columns:
                    draw_segments(fig, data, column, f"{name} · {legend}", index, VALUE_HOVER, dash="dot")
    if fig.data:
        display(fig, key + "_price_chart")
    else:
        st.info("No valid prices in the selected window.")


def curves(data, key):
    section(1, "Forward curve", "Compare Baltic forward assessments across delivery periods and assessment dates.")
    data = data[data.kind.eq("BFA") & data.delivery_start.notna()]
    if data.empty:
        st.info("No forward assessments with confirmed delivery periods.")
        return None

    left, right = st.columns([1, 1.65])
    tenors = [tenor for tenor in TENORS if tenor in set(data.tenor)]
    tenor = left.radio("Delivery", tenors, format_func=TENORS.get, horizontal=True, key=key + "_curve_tenor")
    data = data[data.tenor.eq(tenor)]
    units = list(data[["currency", "unit"]].drop_duplicates().itertuples(index=False, name=None))
    unit = left.selectbox("Quote unit", units, format_func=lambda pair: unit_label(*pair),
                            key=key + "_curve_unit") if len(units) > 1 else units[0]
    data = data[data.currency.eq(unit[0]) & data.unit.eq(unit[1])]
    days = sorted(data.quote_day.unique(), reverse=True)
    defaults = [days[0], *[days[i] for i in (5, 20) if len(days) > i]]
    if len(defaults) == 1 and len(days) > 1:
        defaults.append(days[1])
    selected = right.multiselect(
        "Assessment dates", days, default=defaults, max_selections=4,
        format_func=lambda day: day.strftime("%d %b %Y"), key=key + tenor + str(unit) + "_snapshots",
    )
    if not selected:
        st.info("Select an assessment date to display the curve.")
        return None

    by_day = snapshots(data, sorted(selected, reverse=True))
    fig = chart(unit_label(*unit), height=408)
    fig.update_xaxes(type="category", title="Delivery period", tickangle=-30)
    for index, (day, snapshot) in enumerate(by_day.items()):
        fig.add_scatter(
            x=[delivery_label(tenor, expiry) for expiry in snapshot.index], y=snapshot.price,
            mode="lines+markers", connectgaps=False, name=day.strftime("%d %b %Y"),
            line=dict(color=COLORS[index], dash=DASHES[index], width=3 if index == 0 else 1.8),
            marker=dict(size=6 if index == 0 else 4),
            hovertemplate="%{x}<br>%{y:,.3~f}<extra>%{fullData.name}</extra>",
        )
    st.caption(f"Latest {TENORS[tenor].lower()} forward assessment · {days[0]:%d %b %Y}")
    display(fig, key + "_curve_chart")
    return data, tenor, unit, days, max(selected)


def curve_moves(curve, key):
    section(3, "Curve repricing", "Price change for the same delivery contract versus one and five published assessments earlier.")
    if not curve:
        st.info("Select a forward-curve snapshot to compare assessments.")
        return
    data, tenor, unit, days, anchor = curve
    index = days.index(anchor)
    offsets = [step for step in (1, 5) if index + step < len(days)]
    if not offsets:
        st.info("No earlier assessment is available for this snapshot.")
        return

    prior_days = [days[index + step] for step in offsets]
    by_day = snapshots(data, [anchor, *prior_days])
    current = by_day[anchor].price.dropna()
    fig = chart(unit_label(*unit), height=315)
    fig.update_layout(barmode="group")
    fig.update_xaxes(type="category", title="Delivery period", tickangle=-30)
    for step, previous in zip(offsets, prior_days):
        delta = (current - by_day[previous].price.reindex(current.index)).dropna()
        if not delta.empty:
            fig.add_bar(
                x=[delivery_label(tenor, date) for date in delta.index], y=delta,
                name=f"{step} assessment{'s' if step > 1 else ''} · vs {previous:%d %b}",
                marker_color=COLORS[0 if step == 1 else 1],
                hovertemplate="%{x}<br>%{y:+,.3~f}<extra>%{fullData.name}</extra>",
            )
    if fig.data:
        st.caption(f"As of {anchor:%d %b %Y} · Matching delivery periods only")
        display(fig, key + "_curve_moves")
    else:
        st.info("The selected assessments have no matching delivery contracts.")


def spreads(local, fixed, rolling, key):
    section(4, "Relative value", "Compare two compatible legs as a price spread or ratio.")
    if not local:
        st.info("No series are available for comparison.")
        return
    all_series = options(fixed, rolling)
    left, middle, right = st.columns([2, 2, 1])
    local_keys = sorted(local, key=lambda k: (
        k[2] == "Spot", k[5] != "month", k[6] != "Rolling", -local[k][1].quote_day.max().value,
    ))
    preferred = rolling_leg(local, "+1MON") or local_keys[0]
    a_key = left.selectbox("Leg A", local_keys, index=local_keys.index(preferred),
                          format_func=lambda k: series_name(k, local), key=key + "_a")
    compatible = [k for k in all_series if k[3:5] == a_key[3:5] and k != a_key]
    if not compatible:
        st.info("No second leg shares this quote unit.")
        return
    compatible.sort(key=lambda k: (
        k[:2] != a_key[:2], k[6] != a_key[6], k[5] != a_key[5], -all_series[k][1].quote_day.max().value,
    ))
    next_month = rolling_leg(local, "+2MON", a_key[3:5])
    default_b = compatible.index(next_month) if a_key == preferred and next_month in compatible else 0
    b_key = middle.selectbox("Leg B", compatible, index=default_b, format_func=lambda k: series_name(k, all_series, full=True),
                             key=key + "_b_" + str(a_key))
    method = right.radio("Measure", ["Spread", "Ratio"], horizontal=True, key=key + "_direction")
    cross = a_key[0] != b_key[0] and a_key[2] == b_key[2] == "BFA"
    a_rows, b_rows = all_series[a_key][1], all_series[b_key][1]
    scope = fixed[(fixed.vessel.eq(a_key[0]) & fixed["product"].eq(a_key[1])) |
                  (fixed.vessel.eq(b_key[0]) & fixed["product"].eq(b_key[1]))]
    days = pd.DatetimeIndex(sorted(set(scope.quote_day) | set(a_rows.quote_day) | set(b_rows.quote_day)))
    with st.popover("Comparison options"):
        bounds = history_bounds(days, key + "_spread_range")
        show_stats = st.checkbox("Show rolling mean and 10th–90th percentiles", key=key + "_stats")
        align = st.checkbox("Match cross-vessel deliveries", value=True, key=key + "_align") if cross else False
    if bounds is None:
        st.info("Select both dates to display the comparison.")
        return
    divide = method == "Ratio"
    unit = "x" if divide else unit_label(*a_key[3:5])
    direction = "A ÷ B" if divide else "A − B"
    result = compare(timeline(a_rows, days), timeline(b_rows, days), divide, align, window=20).loc[bounds[0]:bounds[1]]
    fig = chart(unit, height=335)
    hover = VALUE_HOVER + "<br>A %{customdata[0]:,.3~f} · %{customdata[2]}<br>B %{customdata[1]:,.3~f} · %{customdata[3]}"
    draw_segments(fig, result, "price", direction, 0, hover, ["a", "b", "a_period", "b_period"])
    mark_rolls(fig, result, ["a_period", "b_period"])
    if show_stats:
        for index, (column, name) in enumerate([("mean", "20-pair mean"), ("p10", "10th percentile"),
                                                 ("p90", "90th percentile")], 1):
            draw_segments(fig, result, column, name, index, VALUE_HOVER)
        if result["mean"].notna().sum() == 0:
            st.caption("Statistics need 20 matched assessments within the same roll segment.")
    if fig.data:
        st.caption(f"{direction} · Same assessment date" + (" · Same delivery period" if align else ""))
        display(fig, key + "_spread_chart")
        if show_stats and result["mean"].notna().any():
            latest = result.dropna(subset=["price"]).iloc[-1]
            score = f"{latest['z']:+.2f}" if pd.notna(latest["z"]) else "unavailable"
            st.caption(f"Latest matched assessment · Z-score {score} · {int(latest['samples'])} matched observations")

    else:
        st.info("No matched observations for these legs and comparison settings.")


def basis_and_slope(local, days, key):
    front = rolling_leg(local, "+1MON")
    unit = front[3:5] if front else None
    spot = next((leg for leg in local if leg[2] == "Spot" and leg[3:5] == unit), None)
    second = rolling_leg(local, "+2MON", unit) if unit else None
    studies = [
        (5, "Spot basis", "M+1 forward assessment minus same-benchmark spot on the same date.", spot),
        (6, "Front curve slope", "M+1 minus M+2. Positive values indicate nearby backwardation.", second),
    ]
    for number, title, description, leg in studies:
        section(number, title, description)
        if not front or not leg:
            st.info("The matching assessment series are unavailable for this benchmark.")
            continue
        a, b = (timeline(local[item][1], days) for item in (front, leg))
        result = compare(a, b, divide=False, align=False, window=20)
        if result.price.notna().sum() == 0:
            st.info("No overlapping assessment dates for these series.")
            continue
        fig = chart(unit_label(*unit), height=280)
        draw_segments(fig, result, "price", "M+1 − Spot" if number == 5 else "M+1 − M+2", 0, VALUE_HOVER)
        mark_rolls(fig, result, ["a_period", "b_period"])
        display(fig, key + f"_study_{number}")
