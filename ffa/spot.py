"""Spot and calendar-seasonal views using the existing dashboard chart system."""
import pandas as pd
import streamlit as st

from .analytics import indicators, monthly_spot, seasonal, timeline, unit_label
from .preferences import choice, toggle
from .ui import COLORS, VALUE_HOVER, bollinger_history, chart, date_axis, display, draw_segments, mark_rolls, series_name


def monthly_overlay(fig, source, bounds):
    values = monthly_spot(source).loc[bounds[0]:bounds[1]].copy()
    values["Month"] = values["month"].dt.strftime("%b %Y")
    values["First"] = values.first_day.dt.strftime("%d %b %Y")
    values["Last"] = values.last_day.dt.strftime("%d %b %Y")
    fig.add_scatter(
        x=values.index, y=values.price, name="Spot monthly mean", mode="lines+markers", connectgaps=False,
        line=dict(color=COLORS[4], width=2.5, dash="longdash"), marker=dict(size=6, symbol="square"),
        customdata=values[["Month", "samples", "First", "Last"]],
        hovertemplate="<b>Spot monthly mean · %{customdata[0]}</b><br>%{y:,.3~f}"
                      "<br>%{customdata[1]} assessments<br>%{customdata[2]} – %{customdata[3]}<extra></extra>",
    )


def spot_analysis(local, days, bounds, key):
    choices = [leg for leg in local if leg[2] == "Spot"]
    if not choices:
        st.info("No Spot assessments for this benchmark.")
        return
    leg = choice("Spot quote unit", choices, key + "_unit", format_func=lambda item: unit_label(*item[3:5])) if len(choices) > 1 else choices[0]
    data = indicators(timeline(local[leg][1], days))
    visible = data.loc[bounds[0]:bounds[1]].dropna(subset=["price"])
    if visible.empty:
        st.info("No Spot assessments in the selected dates.")
        return
    last = visible.iloc[-1]
    for column, title, container in zip(("price", "ma5", "ma20"), ("Spot", "MA5", "MA20"), st.columns(3)):
        container.metric(title, f"{last[column]:,.2f}" if pd.notna(last[column]) else "—")
    unit = unit_label(*leg[3:5])
    st.caption(f"{unit} · Latest visible Spot assessment {visible.index[-1]:%d %b %Y}. "
               "Moving averages count valid quotation days; missing quotes are not filled.")
    trend, bands = st.columns(2)
    with trend:
        st.markdown("**Trend · Spot, MA5 and MA20**")
        with st.popover("Trend settings"):
            show_ma5 = toggle("MA5", key + "_trend_ma5", default=True)
            show_ma20 = toggle("MA20", key + "_trend_ma20", default=True)
        st.caption("5- and 20-day means, using valid quotes.")
        fig = chart(unit, height=285)
        for column, name, index in (("price", "Spot", 0), ("ma5", "MA5", 1), ("ma20", "MA20", 2)):
            if column == "ma5" and not show_ma5 or column == "ma20" and not show_ma20:
                continue
            draw_segments(fig, data.loc[bounds[0]:bounds[1]], column, name, index, VALUE_HOVER)
        date_axis(fig, bounds)
        display(fig, key + "_trend")
    with bands:
        bollinger_history({"Spot": [("Spot", data, "price")]}, bounds, unit, key + "_standalone", compact=True)


def seasonality(local, days, years, key):
    choices = [leg for leg in local if leg[2] == "Spot" or leg[6] == "Rolling"]
    if not choices or not years:
        st.info("Select years with Spot or Rolling assessments.")
        return
    choices.sort(key=lambda leg: (leg[2] != "Spot", leg[5] != "month", local[leg][0]))
    leg = choice("Seasonal series", choices, key + "_series",
                 format_func=lambda item: f"{series_name(item, local)} · {'Spot' if item[2] == 'Spot' else 'Rolling'} · {unit_label(*item[3:5])}")
    frequency = choice("Seasonal frequency", ["Daily", "Monthly mean"] if leg[2] == "Spot" else ["Daily"],
                       key + str(leg) + "_frequency", control="segmented_control")
    source = local[leg][1]
    full = timeline(source, days)
    if frequency == "Monthly mean":
        view = monthly_spot(source)
        view["Assessment"] = view.index.strftime("%d %b %Y")
        view.index = pd.DatetimeIndex(view["month"])
        view["segment"] = 1
    else:
        view = full.copy()
        view["Assessment"] = view.index.strftime("%d %b %Y")
    aligned = seasonal(view)
    fig = chart(unit_label(*leg[3:5]), height=345)
    fig.update_xaxes(title="Calendar month / day", tickformat="%b", dtick="M1", tick0="2000-01-01",
                     range=["2000-01-01", "2000-12-31"])
    coverage = []
    for index, year in enumerate(sorted(years, reverse=True)):
        actual = source[source.quote_day.dt.year.eq(year) & source.price.notna()]
        if actual.empty:
            continue
        part = aligned[aligned.year.eq(year)].set_index("season_day")
        coverage_text = f"{actual.quote_day.min():%d %b %Y} – {actual.quote_day.max():%d %b %Y} · {len(actual)} assessments"
        draw_segments(fig, part, "price", str(year), index, "%{customdata[0]}<br><b>%{y:,.3~f}</b><br>%{meta[0]}",
                      ["Assessment"], meta=[coverage_text])
        if leg[6] == "Rolling":
            mark_rolls(fig, part, name=str(year), index=index, seasonal_axis=True)
        coverage.append({"Year": year, "First assessment": f"{actual.quote_day.min():%d %b %Y}",
                         "Last assessment": f"{actual.quote_day.max():%d %b %Y}", "Assessments": len(actual)})
    if fig.data:
        display(fig, key + "_seasonality")
    else:
        st.info("No assessments for this series in the selected years.")
    if len(coverage) < 3:
        st.info("Fewer than three years are available for this series. Compare at least three, preferably five, well-covered years before interpreting seasonality.")
    st.caption("Observed prices only. Partial years are not extrapolated; coverage does not certify a complete trading calendar. "
               "February 29 keeps its own calendar position. " +
               ("Rolling prices are unadjusted, with breaks and dated diamonds at contract rolls." if leg[6] == "Rolling" else
                "Monthly means use stored daily assessments; the latest month is month-to-date." if frequency == "Monthly mean" else ""))
    if coverage:
        newest = coverage[0]
        st.caption(f"{newest['Year']} through {newest['Last assessment']}. Exact yearly coverage and sample counts are shown on hover.")
