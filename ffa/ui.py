import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .analytics import delivery_label, indicators, snapshots, timeline, unit_label
from .model import VESSELS

COLORS = ["#177b81", "#3a59a6", "#b67132", "#784ea4", "#52845b", "#a74f6a"]
DASHES = ["solid", "dash", "dot", "dashdot"]
TENORS = {"month": "Monthly", "quarter": "Quarterly", "year": "Calendar year"}
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


def date_axis(fig, bounds):
    padding = pd.Timedelta(hours=12)
    fig.update_xaxes(type="date", range=[bounds[0] - padding, bounds[1] + padding])


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


def prices(local, days, bounds, key):
    section(6, "Contract History", "Track rolling or fixed contracts alongside the matching spot assessment.")
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
        overlays = st.multiselect("Overlays", ["5-day MA", "20-day MA", "Bollinger bands"], key=key + "_overlays")
        add_spot = st.checkbox("Show matching spot", value=True, key=key + "_spot") if spot else False
    if add_spot and spot not in selected:
        selected = [*selected, spot]
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
        date_axis(fig, bounds)
        display(fig, key + "_price_chart")
    else:
        st.info("No valid prices in the selected window.")



def curves(data, market, bounds, key):
    section(1, "Forward Curve", "Compare assessment snapshots and compatible benchmarks by delivery period.")
    forward = data[data.kind.eq("BFA") & data.delivery_start.notna() & data.quote_day.between(*bounds)]
    if forward.empty:
        st.info("No confirmed forward assessments in this date range.")
        return None

    left, right = st.columns([1, 1.65])
    tenors = [tenor for tenor in TENORS if tenor in set(forward.tenor)]
    tenor = left.radio("Delivery", tenors, format_func=TENORS.get, horizontal=True, key=key + "_curve_tenor")
    forward = forward[forward.tenor.eq(tenor)]
    units = list(forward[["currency", "unit"]].drop_duplicates().itertuples(index=False, name=None))
    unit = left.selectbox("Quote unit", units, format_func=lambda pair: unit_label(*pair),
                          key=key + "_curve_unit") if len(units) > 1 else units[0]
    forward = forward[forward.currency.eq(unit[0]) & forward.unit.eq(unit[1])]
    days = sorted(forward.quote_day.unique(), reverse=True)
    selected = right.multiselect(
        "Assessment dates", days, default=days[:2], max_selections=4,
        format_func=lambda day: day.strftime("%d %b %Y"), key=key + tenor + str(unit) + "_snapshots",
    )
    if not selected:
        st.info("Select at least one assessment date.")
        return None

    vessel, benchmark = forward.iloc[0][["vessel", "product"]]
    compatible = market[
        market.kind.eq("BFA") & market.tenor.eq(tenor) & market.delivery_start.notna()
        & market.currency.eq(unit[0]) & market.unit.eq(unit[1]) & market.quote_day.isin(selected)
        & ~(market.vessel.eq(vessel) & market["product"].eq(benchmark))
    ]
    peers = sorted(set(compatible[["vessel", "product"]].itertuples(index=False, name=None)))
    with st.popover("Curve options"):
        selected_peers = st.multiselect(
            "Overlay benchmarks", peers, max_selections=2,
            format_func=lambda item: f"{VESSELS[item[0]]} · {item[1]}", key=key + tenor + str(unit) + "_peers",
            help="Only benchmarks with the same unit, tenor and at least one selected assessment date are listed.",
        )
        base = forward[forward.quote_day.isin(selected)]
        overlays = {
            f"{VESSELS[v]} · {name}": compatible[compatible.vessel.eq(v) & compatible["product"].eq(name)]
            for v, name in selected_peers
        }
        dates = sorted(set(base.delivery_start).union(*(set(rows.delivery_start) for rows in overlays.values())))
        delivery = st.select_slider(
            "Delivery Range", dates, value=(dates[0], dates[-1]),
            format_func=lambda day: delivery_label(tenor, day), key=key + tenor + str(unit) + "_delivery",
        ) if len(dates) > 1 else (dates[0], dates[0])

    fig = chart(unit_label(*unit), height=408)
    order = [delivery_label(tenor, day) for day in dates if delivery[0] <= day <= delivery[1]]
    fig.update_xaxes(type="category", title="Delivery period", tickangle=-30, categoryorder="array", categoryarray=order)
    trace = 0
    for name, rows in [(benchmark, base), *overlays.items()]:
        for day, snapshot in snapshots(rows, sorted(selected, reverse=True)).items():
            prices = snapshot.price.loc[delivery[0]:delivery[1]]
            if prices.notna().sum() == 0:
                continue
            fig.add_scatter(
                x=[delivery_label(tenor, expiry) for expiry in prices.index], y=prices,
                mode="lines+markers", connectgaps=False, name=f"{name} · {day:%d %b %y}",
                line=dict(color=COLORS[trace % len(COLORS)], dash=DASHES[trace % len(DASHES)], width=2.5),
                marker=dict(size=5),
                hovertemplate="%{x}<br>%{y:,.3~f}<extra>%{fullData.name}</extra>",
            )
            trace += 1
    if fig.data:
        display(fig, key + "_curve_chart")
    else:
        st.info("No prices in the selected delivery range.")
    return forward, tenor, unit, days, selected, delivery


def curve_moves(curve, key):
    section(3, "Curve Repricing", "Compare two assessment dates for the same fixed delivery contracts.")
    if curve is None:
        st.info("Select a forward curve first.")
        return
    data, tenor, unit, days, selected, delivery = curve
    if len(days) < 2:
        st.info("Two forward assessment dates are needed for repricing.")
        return

    left, right = st.columns(2)
    anchor_default = max(selected)
    anchor = left.selectbox("To assessment", days, index=days.index(anchor_default),
                            format_func=lambda day: day.strftime("%d %b %Y"), key=key + tenor + "_move_to")
    prior = [day for day in days if day < anchor]
    if not prior:
        st.info("Choose a later assessment as the end date.")
        return
    previous = right.selectbox("From assessment", prior, format_func=lambda day: day.strftime("%d %b %Y"),
                               key=key + tenor + f"_move_from_{anchor:%Y%m%d}")
    views = snapshots(data, [anchor, previous])
    delta = (views[anchor].price - views[previous].price).loc[delivery[0]:delivery[1]].dropna()
    if delta.empty:
        st.info("These assessments share no fixed contracts in the delivery range.")
        return

    fig = chart(unit_label(*unit), height=315)
    fig.update_xaxes(type="category", title="Delivery period", tickangle=-30)
    fig.add_bar(x=[delivery_label(tenor, day) for day in delta.index], y=delta,
                marker_color=["#177b81" if value >= 0 else "#a74f6a" for value in delta],
                hovertemplate="%{x}<br>%{y:+,.3~f}<extra>Assessment change</extra>")
    display(fig, key + "_curve_moves")

    if len(days) >= 3:
        with st.expander("Daily repricing heatmap"):
            matrix = data.pivot(index="quote_day", columns="delivery_start", values="price").sort_index()
            changes = matrix.diff().loc[:, delivery[0]:delivery[1]].dropna(how="all").tail(20)
            if changes.empty:
                st.info("No consecutive assessments share delivery contracts in this range.")
            else:
                heatmap = chart("Assessment date")
                heatmap.add_heatmap(
                    x=[delivery_label(tenor, day) for day in changes.columns],
                    y=changes.index.strftime("%d %b %Y"), z=changes.to_numpy(), zmid=0,
                    colorscale=[[0, "#a74f6a"], [.5, "#f6f8fb"], [1, "#177b81"]],
                    colorbar=dict(title=unit_label(*unit)),
                    hovertemplate="%{y} · %{x}<br>%{z:+,.3~f}<extra></extra>",
                )
                heatmap.update_xaxes(type="category", title="Delivery period")
                display(heatmap, key + "_heatmap")
