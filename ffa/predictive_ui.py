"""Compact, English-only research views within Price Explorer."""
import numpy as np
import plotly.graph_objects as go
import streamlit as st

from .predictive import (HORIZONS, MIN_CONDITIONAL, MIN_RELATION, MIN_TEST, MIN_TRAIN_DATES,
                         STAGES, build_samples, relationship, score, select_samples, walk_forward)
from .preferences import choice
from .selection import contract_choice
from .ui import COLORS, chart, date_axis, display

VIEWS = ("Price history", "Daily changes", "Lead / lag", "Forecast backtest")


@st.cache_data(max_entries=3, show_spinner=False)
def _dataset(stamp, scope, _fixed):
    return build_samples(_fixed)


@st.cache_data(max_entries=8, show_spinner=False)
def _backtest(stamp, scope, contract, horizon, historical, selected_stage, bounds, strict, _samples):
    return walk_forward(_samples, contract, horizon, pooled=True, historical=historical,
                        selected_stage=selected_stage, bounds=bounds, strict=strict)


def predictive_view(fixed, bounds, key, view, stamp):
    st.markdown("**Spot / BFA research**")
    eligible = fixed[fixed.currency.eq("USD") & fixed.unit.eq("day")]
    contracts = eligible[eligible.kind.eq("BFA") & eligible.delivery_start.notna() & eligible.delivery_end.notna()]
    if contracts.empty or not eligible.kind.eq("Spot").any():
        st.info("Matching Spot and fixed BFA quotes in USD/day are required.")
        return
    with st.popover("Research settings"):
        mode = choice("Validation", ["Date replay", "Verified timestamps"], key + "_validation_v2", "Date replay",
                      control="radio")
        tenor = choice("Contract type", list(dict.fromkeys(contracts.tenor)), key + "_tenor", "month",
                       format_func=lambda item: {"month": "Monthly", "quarter": "Quarterly", "year": "Annual"}.get(item, item))
        st.caption("Date replay assumes quotes were known at date-end; publication times and historical revisions are unverified. "
                   "Verified mode requires every feature to be available by 23:59:59 UTC on the quote date.")
        st.caption(f"Models use the same complete cases. Training needs {MIN_TRAIN_DATES} distinct dates and three expired peers, "
                   "matched by benchmark, unit, contract type and exact months to delivery. Training labels end before each forecast; "
                   "scaling is fitted on training data only. Ridge alpha is fixed at 10. Missing quotes are never filled.")
    strict = mode == "Verified timestamps"
    contracts = contracts[contracts.tenor.eq(tenor)].drop_duplicates("identity").sort_values("delivery_start")
    labels = contracts.set_index("identity").delivery_start.dt.strftime("%b %Y").to_dict()
    upcoming = contracts[contracts.delivery_start.gt(eligible.quote_day.max())]
    contract = (upcoming if not upcoming.empty else contracts).iloc[0].identity
    historical = False
    selected_stage = "all"
    if view != "Daily changes":
        sample = choice("Sample", ["Historical peers", "Selected contract"], key + "_sample_v2", "Historical peers",
                        control="segmented_control")
        historical = sample == "Historical peers"
        selected_stage = choice("Delivery distance", STAGES, key + "_distance_" + sample,
                                "m1" if historical else "all", format_func=STAGES.get)
    if not historical:
        contract=contract_choice("Fixed contract",contracts,key+"_contract_"+tenor,contract)
    scope = (eligible.vessel.iloc[0], eligible["product"].iloc[0], "USD", "day", tenor)
    cache_stamp = (stamp, len(eligible))
    data = _dataset(cache_stamp, scope, eligible[eligible.kind.eq("Spot") | eligible.tenor.eq(tenor)])
    if data.empty:
        st.info("No paired quote history is available for this benchmark.")
        return
    st.caption(f"{mode} | {bounds[0]:%d %b %Y} – {bounds[1]:%d %b %Y} | "
               + (f"Historical {tenor} contracts · {STAGES[selected_stage]}" if historical else f"Fixed contract · {labels[contract]}"))
    if not strict:
        st.caption("Publication times are unverified. Replay results are exploratory, not verified predictive evidence.")
    elif not data.pit.any():
        st.info("Historical availability timestamps are missing. Use Date replay in Research settings to explore the data.")
        return
    selected = select_samples(data, None if historical else contract, selected_stage, strict, bounds)
    if historical and view == "Lead / lag":
        selected = selected[selected.delivery_end.lt(bounds[1])]
        selected = selected.sort_values(["date", "offset", "contract"]).drop_duplicates("date")
    if historical:
        st.caption("Peer results are separate from any single contract. One contract per date is selected by earliest delivery within the maturity group; future targets stay in that fixed contract.")
    if view == "Daily changes":
        _changes(selected, bounds, key)
    elif view == "Lead / lag":
        _lead_lag(selected, key)
    else:
        horizon = choice("Forecast horizon", HORIZONS, key + "_horizon", 1,
                         control="segmented_control", format_func=lambda h: f"{h}D")
        with st.spinner("Running walk-forward validation…"):
            predictions = _backtest(cache_stamp, scope, contract, horizon, historical, selected_stage, bounds, strict, data)
        _forecast(predictions, strict, horizon, key)


def _changes(data, bounds, key):
    pairs = data[["date", "spot_d0", "bfa_d0"]].dropna()
    n = len(pairs)
    if not n:
        st.info("No consecutive paired quotes in this window. Choose a longer History window or another fixed contract.")
        return
    fig = chart("Daily change · USD/day", height=360)
    for column, name, color in (("spot_d0", "Spot Δ", COLORS[0]), ("bfa_d0", "BFA Δ", COLORS[1])):
        fig.add_scatter(x=data.date, y=data[column], mode="lines+markers", name=name,
                        line=dict(color=color), marker_size=3, connectgaps=False,
                        hovertemplate="%{x|%d %b %Y}<br>%{y:,.2f} USD/day<extra>%{fullData.name}</extra>")
    date_axis(fig, bounds)
    display(fig, key + "_changes")
    st.caption(f"{n} paired sessions · absolute daily changes in USD/day, not percentage returns.")
    if n < MIN_RELATION or pairs.spot_d0.std() == 0 or pairs.bfa_d0.std() == 0:
        st.caption(f"Descriptive statistics need {MIN_RELATION} paired sessions with variation in both series.")
        return
    slope = pairs.spot_d0.cov(pairs.bfa_d0) / pairs.spot_d0.var()
    a, b, c = st.columns(3)
    a.metric("Same-day correlation", f"{pairs.spot_d0.corr(pairs.bfa_d0):.2f}")
    b.metric("BFA / Spot response", f"{slope:.2f}")
    c.metric("Same direction", f"{(np.sign(pairs.spot_d0) == np.sign(pairs.bfa_d0)).mean():.1%}")
    st.caption("Response is the fitted BFA change per 1 USD/day Spot change, with an intercept. Same-day association does not establish prediction.")


def _lead_lag(data, key):
    result = relationship(data)
    measure = choice("Measure", ["corr", "up", "down"], key + "_lead_measure", "corr",
                     format_func=lambda item: {"corr": "Correlation", "up": "BFA up | Spot up", "down": "BFA up | Spot down"}[item])
    if result[measure].notna().sum() == 0:
        st.info("This sample is too short for reliable relationships. Select Historical peers and a longer History window.")
        return
    rows, custom, text = [], [], []
    for lag in range(6):
        line, details, labels = [], [], []
        for h in HORIZONS:
            row = result[result.horizon.eq(h) & result.lag.eq(lag)].iloc[0]
            value = row[measure]
            count = int(row.n if measure == "corr" else row["n_" + measure])
            baseline = f"{row.baseline:.1%}" if np.isfinite(row.baseline) else "—"
            interval = "—" if measure == "corr" else f"{row[measure + '_low']:.1%} – {row[measure + '_high']:.1%}"
            label = "—" if np.isnan(value) else f"{value:.2f}" if measure == "corr" else f"{value:.0%}"
            line.append(value)
            labels.append(f"{label}<br>N={count}")
            details.append([int(row.n), int(row.n_up), int(row.n_down), baseline, interval])
        rows.append(line)
        custom.append(details)
        text.append(labels)
    fig = go.Figure(go.Heatmap(z=rows, x=[f"BFA next {h}D" for h in HORIZONS],
                              y=["Spot today"] + [f"Spot {lag}D ago" for lag in range(1, 6)],
                              colorscale="RdBu" if measure == "corr" else "Blues", reversescale=measure == "corr",
                              zmin=-1 if measure == "corr" else 0, zmax=1, zmid=0 if measure == "corr" else .5,
                              customdata=custom, text=text, texttemplate="%{text}", hoverongaps=False,
                              hovertemplate="%{y} → %{x}<br>Value %{z:.3f}<br>Paired N=%{customdata[0]}"
                                            "<br>Spot up/down N=%{customdata[1]} / %{customdata[2]}"
                                            "<br>Unconditional BFA up %{customdata[3]}<br>Probability 95% interval %{customdata[4]}<extra></extra>"))
    fig.update_layout(height=390, margin=dict(l=20, r=20, t=15, b=30), yaxis_autorange="reversed")
    display(fig, key + "_lead_lag")
    st.caption(f"Correlation needs {MIN_RELATION} pairs; conditional probabilities need {MIN_CONDITIONAL} observations per Spot direction. "
               "— indicates a small sample. Future change = BFA(t+h) − BFA(t); flat is not up. Association is not out-of-sample prediction.")
    with st.popover("Method notes"):
        st.caption("Rows use individual daily Spot changes at t through t−5. Probability intervals are descriptive Wilson intervals. "
                   "Overlapping labels, serial dependence and 18 exploratory relationships prevent interpreting these as significance tests.")


def _forecast(predictions, strict, horizon, key):
    independent, metrics, extra = score(predictions)
    a, b = st.columns(2)
    a.metric("Non-overlapping test cases", len(independent))
    b.metric("Test contracts", independent.contract.nunique() if not independent.empty else 0)
    if metrics.empty:
        st.info(f"{len(independent)} of {MIN_TEST} required independent {horizon}D cases are available. "
                "Use Historical peers and 3Y or All history for more coverage.")
        return
    measure = choice("Score", ["mae", "accuracy"], key + "_score_measure", "mae",
                     format_func=lambda item: "Mean absolute error" if item == "mae" else "Direction accuracy")
    shown = metrics if measure == "mae" else metrics[metrics.accuracy.notna()]
    fig = chart("USD/day" if measure == "mae" else "Accuracy", height=330)
    fig.add_bar(x=shown.model, y=shown[measure], marker_color=COLORS[:len(shown)],
                text=[f"{value:,.1f}" if measure == "mae" else f"{value:.1%}" for value in shown[measure]], textposition="outside")
    if measure == "accuracy":
        fig.update_yaxes(range=[0, 1], tickformat=".0%")
    display(fig, key + "_backtest_summary")
    lo, hi = extra["mae_interval"]
    dlo, dhi = extra["accuracy_interval"]
    st.markdown(f"**Adding Spot vs BFA-only:** MAE improvement **{extra['mae_gain']:+,.1f} USD/day** "
                f"(95% interval {lo:+,.1f} to {hi:+,.1f}); direction improvement **{extra['accuracy_gain']:+.1%}** "
                f"({dlo:+.1%} to {dhi:+.1%}).")
    if not strict:
        st.caption("Replay gains are exploratory. A verified predictive edge requires historical availability timestamps and vintage data.")
    elif lo > 0 and dlo > 0:
        st.success("Adding Spot improves both measures in this verified test window. This does not guarantee future performance.")
    else:
        st.info("The verified test does not establish gains in both error and direction.")
    with st.popover("Method notes"):
        st.caption(f"{len(predictions)} daily forecasts; scores use {len(independent)} non-overlapping cases, identical across models. "
                   "Direction is up/flat/down. Unchanged price is an error baseline, not a direction signal. "
                   "MAE measures the error in the future cumulative price change in USD/day.")
        st.caption(f"Positive improvement favours adding Spot. Paired time-block bootstrap: 1,000 draws, block length {extra['block']}, "
                   "fixed seed. An interval containing zero does not establish an improvement. Repeated user exploration is not an untouched final holdout.")
