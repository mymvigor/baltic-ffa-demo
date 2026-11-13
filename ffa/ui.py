import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .analytics import delivery_label, indicators, period_means, snapshots, timeline, unit_label
from .model import VESSELS
from .preferences import choice, multi, toggle

COLORS = ["#177b81", "#3a59a6", "#b67132", "#784ea4", "#52845b", "#a74f6a"]
DASHES = ["solid", "dash", "dot", "dashdot"]
TENORS = {"month": "Monthly", "quarter": "Quarterly", "year": "Calendar year"}
VALUE_HOVER = "%{x|%d %b %Y}<br><b>%{y:,.3~f}</b>"


def section(number, title, description):
    st.markdown(
        f'<div class="ffa-section"><h2><span class="ffa-number">{number:02d}</span>{title}</h2>'
        f'<p>{description}</p></div>', unsafe_allow_html=True,
    )


def chart(y, height=310):
    fig = go.Figure()
    fig.update_layout(
        template="plotly_white", height=height, margin=dict(l=14, r=22, t=40, b=18),
        paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font=dict(family="Arial, sans-serif", color="#243b52", size=12),
        hovermode="closest",
        hoverlabel=dict(bgcolor="#ffffff", bordercolor="#d5dfe8", align="left",
                        font=dict(family="Arial, sans-serif", color="#243b52", size=12),
                        namelength=-1),
        legend=dict(orientation="h", y=1.025, x=0, yanchor="bottom", font_size=11, groupclick="togglegroup"),
        yaxis=dict(title=y, tickformat=",.3~f", gridcolor="#e7edf3", zerolinecolor="#aebfcd", automargin=True),
        xaxis=dict(gridcolor="#eef3f7", automargin=True, tickfont=dict(color="#516478")),
    )
    return fig


def display(fig, key):
    st.plotly_chart(
        fig, theme=None, width="stretch", key=key,
        config={"displaylogo": False, "responsive": True, "modeBarButtonsToRemove": ["toImage", "lasso2d", "select2d"]},
    )


def date_axis(fig, bounds):
    padding = pd.Timedelta(hours=12)
    fig.update_layout(hovermode="closest")
    fig.update_xaxes(type="date", range=[bounds[0] - padding, bounds[1] + padding],
                     hoverformat="%d %b %Y", nticks=7, showspikes=True, spikedash="dot",
                     spikecolor="#9caebc", spikemode="across", spikesnap="hovered data")


def series_name(key, series, full=False):
    name = series[key][0]
    if full:
        return f"{VESSELS[key[0]]} · {key[1]} · {name}"
    return name


def draw_segments(fig, data, column, name, index, hover, custom=None, dash=None, level=False):
    observed = data[data[column].notna()]
    if observed.empty:
        return

    color = COLORS[index % len(COLORS)]
    pattern = dash or DASHES[index % len(DASHES)]
    if level:
        # Contract changes and missing quotes are actual breaks, never dotted links.
        split = data.segment.ne(data.segment.shift()) | data.index.to_series().diff().gt(pd.Timedelta(days=7))
        groups = data.groupby(split.cumsum(), sort=False)
    else:
        groups = data.groupby("segment", sort=False)

    x, y, metadata = [], [], []
    for _, part in groups:
        if not part[column].notna().any():
            continue
        x.extend([*part.index, None])
        y.extend([*part[column], None])
        if custom:
            metadata.extend([*part[custom].itertuples(index=False, name=None), (None,) * len(custom)])

    # A single observation cannot form a line. Keep the value visible in that case.
    fig.add_scatter(
        x=x, y=y, name=name, mode="lines+markers", connectgaps=False,
        line=dict(color=color, width=2.35, dash=pattern), marker=dict(size=2.5 if len(observed) > 1 else 6, color=color),
        legendgroup=name, customdata=metadata if custom else None,
        hovertemplate="<b>%{fullData.name}</b><br>" + hover + "<extra></extra>",
    )
    if level and column == "price":
        mark_rolls(fig, data, column, name, index)


def mark_rolls(fig, data, column="price", name="Series", index=0, seasonal_axis=False):
    if "roll_from" not in data:
        return
    rolls = data[data.roll_from.notna() & data[column].notna()]
    if rolls.empty:
        return
    dates = rolls["quote_day"] if seasonal_axis else rolls.index
    labels = [f"{day:%Y-%m-%d}" for day in dates]
    fig.add_scatter(
        x=rolls.index, y=rolls[column], name=f"{name} · Roll", legendgroup=name, showlegend=False,
        mode="markers+text" if len(rolls) <= 6 else "markers", text=labels, textposition="top center",
        textfont=dict(size=10, color="#516478"),
        marker=dict(size=8, symbol="diamond-open", color=COLORS[index % len(COLORS)]),
        customdata=list(zip(labels, rolls.roll_from, rolls.roll_to)),
        hovertemplate="<b>Contract roll · %{customdata[0]}</b><br>%{customdata[1]}<br>%{customdata[2]}<extra></extra>",
    )


def rolling_leg(local, suffix, unit=None):
    return next((
        key for key in local if key[6] == "Rolling" and key[5] == "month"
        and re.search(re.escape(suffix) + r"(?:#\d+)?$", key[-1])
        and (unit is None or key[3:5] == unit)
    ), None)


def average_history(groups, bounds, unit, key):
    groups = {name: sources for name, sources in groups.items() if sources}
    if not groups:
        return
    with st.container():
        st.markdown("**Period averages**")
        left, right = st.columns(2)
        frequency = choice(
            "Average by", ["Weekly", "Monthly", "Quarterly", "Yearly"],
            key + "_average_frequency", "Monthly", container=left,
        )
        view = choice("Measure", groups, key + "_average_view", container=right) if len(groups) > 1 else next(iter(groups))
        sources, measure_unit = groups[view], unit
        if isinstance(unit, dict):
            measure_unit = unit[view]
        fig = chart(measure_unit, height=255)
        rule = {"Weekly": "W-SUN", "Monthly": "M", "Quarterly": "Q-DEC", "Yearly": "Y-DEC"}[frequency]
        for index, (name, data, column) in enumerate(sources):
            means = period_means(data, column, rule, bounds)
            if means.empty:
                continue
            means = means.set_index("date")
            details = []
            for row in means.itertuples():
                value = f"{row.price:,.3f}".rstrip("0").rstrip(".")
                lines = [row.period, f"Mean  <b>{value}</b>",
                         f"{row.samples} assessments", f"Observed  {row.observed}"]
                lines.extend(note for note in (row.clipped, row.contract) if note)
                details.append("<br>".join(lines))
            means["Details"] = details
            draw_segments(fig, means, "price", name, index, "%{customdata[0]}", ["Details"])
            fig.data[-1].mode = "lines+markers"
            fig.data[-1].marker.size = 5
        if not fig.data:
            st.info("No valid assessments in the selected dates.")
            return
        st.caption("Arithmetic means of available assessments within the selected dates. "
                   "Weeks run Monday–Sunday. Contract rolls are averaged separately. "
                   "Spreads and ratios are calculated daily before averaging. "
                   "Each point is placed on its last observed assessment date.")
        date_axis(fig, bounds)
        display(fig, key + "_averages_chart")


def bollinger_history(groups, bounds, unit, key):
    groups = {name: sources for name, sources in groups.items() if sources}
    if not groups:
        return
    with st.container():
        st.markdown("**Bollinger bands**")
        left, right = st.columns(2)
        view = choice("Measure", groups, key + "_bb_view", container=left) if len(groups) > 1 else next(iter(groups))
        sources = groups[view]
        names = [source[0] for source in sources]
        selected = choice("Series", names, key + "_bb_series", container=right) if len(names) > 1 else names[0]
        controls = st.columns(5)
        show_series = toggle("Series line", key + "_bb_show_series", default=True, container=controls[0])
        show_mean = toggle("20-day MA", key + "_bb_show_mean", default=True, container=controls[1])
        show_upper = toggle("Upper band", key + "_bb_show_upper", default=True, container=controls[2])
        show_lower = toggle("Lower band", key + "_bb_show_lower", default=True, container=controls[3])
        show_shading = toggle("Shading", key + "_bb_show_shading", default=False, container=controls[4])
        if not any((show_series, show_mean, show_upper, show_lower, show_shading)):
            st.info("Select a line or shading to display.")
            return
        name, source, column = next(item for item in sources if item[0] == selected)
        data = source.copy()
        data["price"] = data[column]
        data = indicators(data).loc[bounds[0]:bounds[1]]
        valid = data.upper.notna() & data.lower.notna()
        if not valid.any() and any((show_mean, show_upper, show_lower, show_shading)):
            st.info("Bollinger bands need 20 valid assessments within the same contract segment.")
        measure_unit = unit[view] if isinstance(unit, dict) else unit
        fig = chart(measure_unit, height=285)
        # Shade each continuous run separately so fills cannot bridge rolls or missing quotes.
        breaks = data.segment.ne(data.segment.shift()) | ~valid | ~valid.shift(fill_value=False)
        for index, (_, run) in enumerate(data[valid].groupby(breaks[valid].cumsum())):
            for column, label, enabled in (("lower", "Lower band", show_lower), ("upper", "Upper band", show_upper)):
                if not enabled and not show_shading:
                    continue
                fig.add_scatter(
                    x=run.index, y=run[column], mode="lines", name=label, legendgroup=label,
                    showlegend=index == 0 and enabled,
                    line=dict(color="#6c8a9d", width=1 if enabled else 0, dash="dot"),
                    fill="tonexty" if show_shading and column == "upper" else None,
                    fillcolor="rgba(23,123,129,0.09)", connectgaps=False,
                    hoverinfo=None if enabled else "skip",
                    hovertemplate=("<b>%{fullData.name}</b><br>" + VALUE_HOVER + "<extra></extra>") if enabled else None,
                )
        if show_series:
            draw_segments(fig, data, "price", name, 0, VALUE_HOVER)
        if show_mean:
            draw_segments(fig, data, "ma20", "20-day MA", 1, VALUE_HOVER, dash="dash")
        if not fig.data:
            return
        st.caption("20-assessment mean ± 2 standard deviations (population). "
                   "Valid quotation days only; calculations restart at each contract roll.")
        date_axis(fig, bounds)
        display(fig, key + "_bollinger_chart")


def prices(local, days, bounds, key, mode):
    if not local:
        st.info("No price series are available.")
        return

    left, right = st.columns([1, 2.2])
    all_choices = [k for k in local if k[6] == mode]
    if not all_choices:
        st.info("No contracts for this series type.")
        return
    all_choices.sort(key=lambda k: (
        k[2] == "Spot", k[5] != "month", -local[k][1].quote_day.max().value,
        local[k][1].delivery_start.min() if k[2] == "BFA" else pd.Timestamp.max, local[k][0],
    ))
    preferred = rolling_leg(local, "+1MON") if mode == "Rolling" else None
    preferred = preferred or all_choices[0]
    units = list(dict.fromkeys(k[3:5] for k in [preferred, *all_choices]))
    unit = choice("Quote unit", units, key + mode + "_price_unit", container=left,
                  format_func=lambda pair: unit_label(*pair)) if len(units) > 1 else units[0]
    choices = [k for k in all_choices if k[3:5] == unit and k[2] != "Spot"]
    if not choices:
        st.info("No BFA contracts in this quote unit.")
        return
    selected = multi("Contracts", choices, key + mode + str(unit) + "_legs",
                     [preferred if preferred in choices else choices[0]], 5,
                     format_func=lambda k: series_name(k, local), container=right)
    spot = next((k for k in local if k[2] == "Spot" and k[3:5] == unit), None)
    with st.popover("Chart options"):
        overlays = multi("Overlays", ["5-day MA", "20-day MA", "Bollinger bands"], key + mode + "_overlays", [], 3)
        add_spot = toggle("Show matching spot", key + mode + "_spot") if spot and len(selected) < 5 else False
    if add_spot and spot not in selected:
        selected = [*selected, spot]
    if not selected:
        st.info("Select a contract to display its price history.")
        return

    fig = chart(unit_label(*unit))
    average_sources = []
    for index, leg in enumerate(selected):
        name, source = local[leg]
        history = timeline(source, days)
        data = indicators(history).loc[bounds[0]:bounds[1]].copy()
        data["Delivery"] = [delivery_label(t, d) if pd.notna(t) else "—" for t, d in zip(data.tenor, data.delivery_start)]
        hover = VALUE_HOVER + ("<br>Contract  %{customdata[0]}" if mode == "Rolling" else "")
        draw_segments(fig, data, "price", name, index, hover, ["Delivery"], level=True)
        average_sources.append((name, history, "price"))
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
        bollinger_history({"Prices": average_sources}, bounds, unit_label(*unit), key + mode)
        average_history({"Prices": average_sources}, bounds, unit_label(*unit), key + mode)
    else:
        st.info("No valid prices in the selected window.")



def forward_series(data, tenor):
    return data[data.kind.eq("BFA") & data.delivery_start.notna() & data.tenor.eq(tenor)]


def as_of(days, target):
    return next((day for day in days if day <= target), None)


def curves(data, market, tenor, targets, key):
    forward = forward_series(data, tenor)
    if forward.empty:
        st.info("No forward assessments for this tenor.")
        return
    units = list(forward[["currency", "unit"]].drop_duplicates().itertuples(index=False, name=None))
    unit = choice("Quote unit", units, key + "_curve_unit", format_func=lambda pair: unit_label(*pair)) if len(units) > 1 else units[0]
    forward = forward[forward.currency.eq(unit[0]) & forward.unit.eq(unit[1])]
    days = sorted(forward.quote_day.unique(), reverse=True)
    selected = list(dict.fromkeys(day for target in targets if (day := as_of(days, target)) is not None))
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
        selected_peers = multi(
            "Overlay benchmarks", peers, key + tenor + str(unit) + "_peers", [], 2,
            format_func=lambda item: f"{VESSELS[item[0]]} · {item[1]}",
        ) if peers else []
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

    fig = chart(unit_label(*unit), height=335)
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
                mode="lines" if prices.notna().sum() > 1 else "markers", connectgaps=False,
                name=f"{name} · {day:%d %b %y}",
                line=dict(color=COLORS[trace % len(COLORS)], dash=DASHES[trace % len(DASHES)], width=2.5),
                marker=dict(size=5),
                hovertemplate="<b>%{fullData.name}</b><br>%{x}<br><b>%{y:,.3~f}</b><extra></extra>",
            )
            trace += 1
    if fig.data:
        display(fig, key + "_curve_chart")
        latest_day = max(selected)
        current = base[base.quote_day.eq(latest_day) & base.delivery_start.between(*delivery)].sort_values("delivery_start")
        if len(current) >= 2:
            near, far = current.iloc[0], current.iloc[-1]
            delta = far.price - near.price
            for title, value, container in zip(
                (f"Near · {delivery_label(tenor, near.delivery_start)}", f"Far · {delivery_label(tenor, far.delivery_start)}", "Far − Near"),
                (near.price, far.price, delta), st.columns(3),
            ):
                container.metric(title, f"{value:,.2f}")
            st.caption(f"{latest_day:%d %b %Y} · {unit_label(*unit)}. Endpoint slope; intermediate deliveries follow the curve.")
            detail = snapshots(base, [latest_day])[latest_day].loc[delivery[0]:delivery[1]]
            table = pd.DataFrame({"Assessment": detail.price, "Adjacent delivery spread": detail.price.diff()})
            table.index = [delivery_label(tenor, expiry) for expiry in table.index]
            st.dataframe(table, width="stretch")
    else:
        st.info("No prices in the selected delivery range.")


def curve_changes(data, tenor, mode, targets, lookback, key):
    data = forward_series(data, tenor)
    if data.empty:
        st.info("No forward assessments for this tenor.")
        return
    units = list(data[["currency", "unit"]].drop_duplicates().itertuples(index=False, name=None))
    unit = choice("Quote unit", units, key + "_changes_unit", format_func=lambda pair: unit_label(*pair)) if len(units) > 1 else units[0]
    data = data[data.currency.eq(unit[0]) & data.unit.eq(unit[1])]
    days = sorted(data.quote_day.unique(), reverse=True)
    if len(days) < 2:
        st.info("Two forward assessment dates are needed.")
        return

    if mode == "Heatmap":
        matrix = data.pivot(index="quote_day", columns="delivery_start", values="price").sort_index()
        changes = matrix.diff().tail(lookback).dropna(axis=1, how="all")
        if changes.empty:
            st.info("No consecutive assessments share delivery contracts.")
            return
        fig = chart("Assessment date", height=360)
        fig.add_heatmap(x=[delivery_label(tenor, day) for day in changes.columns],
                        y=changes.index.strftime("%d %b %Y"), z=changes.to_numpy(), zmid=0,
                        colorscale=[[0, "#a74f6a"], [.5, "#f6f8fb"], [1, "#177b81"]],
                        colorbar=dict(title=unit_label(*unit)),
                        hovertemplate="<b>%{y}</b><br>%{x}<br><b>%{z:+,.3~f}</b><extra></extra>")
        fig.update_xaxes(type="category", title="Delivery period")
        fig.update_yaxes(nticks=10)
        display(fig, key + "_heatmap")
        return

    anchor, previous = (as_of(days, target) for target in targets)
    if anchor is None or previous is None or anchor <= previous:
        st.info("Choose two different assessment dates in chronological order.")
        return
    views = snapshots(data, [anchor, previous])
    delta = (views[anchor].price - views[previous].price).dropna()
    if delta.empty:
        st.info("These assessments share no fixed delivery contracts.")
        return

    st.caption(f"{previous:%d %b %Y} → {anchor:%d %b %Y} · Matched fixed deliveries")
    fig = chart(unit_label(*unit), height=300)
    fig.update_xaxes(type="category", title="Delivery period", tickangle=-30)
    fig.update_yaxes(autorangeoptions=dict(include=0))
    fig.update_layout(uniformtext_minsize=9, uniformtext_mode="hide")
    fig.add_bar(x=[delivery_label(tenor, day) for day in delta.index], y=delta, texttemplate="%{y:+,.3~f}",
                textposition="outside", cliponaxis=False,
                marker_color=["#177b81" if value >= 0 else "#a74f6a" for value in delta],
                hovertemplate=f"<b>%{{x}}</b><br>{previous:%d %b} – {anchor:%d %b %Y}"
                              "<br><b>%{y:+,.3~f}</b><extra></extra>")
    display(fig, key + "_curve_moves")
