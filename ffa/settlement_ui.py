"""Settlement research lives independently of the existing short-horizon models."""
from datetime import datetime, timezone
from hashlib import sha256
import json

import numpy as np
import pandas as pd
import streamlit as st

from .analytics import delivery_label
from .preferences import calendar, choice, multi
from .settlement import (BASIC, FACTORS, SUPPORTED, WINDOWS, eligible_years, exploratory_replay, fit_adjustment,
                         forecast, metrics, replay, spot_history)
from .ui import COLORS, chart, date_axis, display

MIN_TEST = 12
VALIDATION_VIEWS = ("Forecast vs Actual", "Forecast Error", "Model Comparison", "Reference Window Comparison")


@st.cache_data(max_entries=8, show_spinner=False)
def _replay(revision, scope, issue, month, windows, offsets, custom, strict, _quotes):
    return replay(_quotes, issue, month, windows, offsets, custom, strict)


def _log(key, config, month, issue, value, bfa, model, observed_through, revision):
    if issue != observed_through or not np.isfinite(value):
        return
    memory = st.session_state.setdefault("_ffa_preferences", {})
    name = sha256(key.encode()).hexdigest()[:12]
    log = memory.setdefault(name, {"configs": {}, "forecasts": {}})
    serialized = json.dumps(config, sort_keys=True)
    version = sha256(serialized.encode()).hexdigest()[:12]
    data_version = sha256(str(revision).encode()).hexdigest()[:16]
    record_key = sha256(f"{version}/{issue}/{month}/{model}/{data_version}".encode()).hexdigest()[:16]
    log["configs"].setdefault(version, config)
    log["forecasts"].setdefault(record_key, dict(version=version, issue=str(issue.date()),
        delivery=str(month), price=round(value, 4), bfa=None if pd.isna(bfa) else float(bfa),
        model=model, data_version=data_version, created_at=datetime.now(timezone.utc).isoformat()))


def _restore(key):
    name = sha256(key.encode()).hexdigest()[:12]
    log = st.session_state.setdefault("_ffa_preferences", {}).get(name, {})
    version = "Current settings"
    saved = log.get("configs", {})
    if saved:
        with st.popover("Saved configurations"):
            version = choice("Restore configuration", [version, *reversed(saved)], key + "_restore")
            forecasts = list(log.get("forecasts", {}).values())
            if forecasts:
                latest = forecasts[-1]
                st.caption(f"{len(forecasts)} saved forecasts · latest logged {latest['issue']} · {latest['delivery']} · {latest['price']:,.0f} USD/day")
            st.download_button("Export forecast history", json.dumps(log, indent=2), "settlement-forecasts.json",
                               mime="application/json", key=key + "_export")
            st.caption("Saved in this browser when a current forecast is calculated. Replay dates never create records.")
    return version, saved.get(version, {})


def settlement_view(quotes, revision, vessel, benchmark, key, synthetic=False):
    if benchmark not in SUPPORTED and not synthetic:
        st.info("Settlement forecasting is available for supported dry time-charter basket indices.")
        return
    quotes = quotes[quotes.currency.eq("USD") & quotes.unit.eq("day")]
    if quotes.empty:
        st.info("No USD/day quotes for this benchmark.")
        return
    spot = spot_history(quotes)
    forward = quotes[quotes.kind.eq("BFA") & quotes.delivery_start.notna() & quotes.delivery_end.notna()]
    if spot.empty or forward.empty:
        st.info("Spot and fixed BFA history are needed.")
        return
    model = choice("Forecast", ["Basic Models", "Exploratory Model"], key + "_view", control="segmented_control")
    journal = key + "_" + model
    restored, config = _restore(journal)
    state = journal + "_" + restored
    latest = spot.index[-1]
    with st.popover("Advanced Settings"):
        issue = pd.Timestamp(calendar("Forecast date", state+"_issue", latest.date(), spot.index[0].date(), latest.date()))
        validation = choice("Validation data", ["Date replay", "Verified timestamps"], state+"_strict",
                            config.get("validation", "Date replay"))
    strict = validation == "Verified timestamps"
    current_spot = spot_history(quotes, issue) if strict else spot
    available = forward[forward.quote_day.le(issue) & forward.delivery_end.ge(issue)].copy()
    if strict:
        cutoff = issue.tz_localize("UTC") + pd.Timedelta(days=1)
        available = available[available.available_at.lt(cutoff)]
    available = available.sort_values("quote_day").drop_duplicates("identity", keep="last").sort_values("delivery_start")
    if available.empty:
        st.info("No eligible BFA snapshots at this forecast date.")
        return
    contracts = available.set_index("identity")
    labels = {identity: delivery_label(row.tenor, row.delivery_start) for identity, row in contracts.iterrows()}
    upcoming = available[available.tenor.eq("month") & available.delivery_start.gt(issue)]
    default_contract = upcoming.identity.iloc[0] if not upcoming.empty else available.identity.iloc[0]
    contract = choice("Target contract", labels, state+"_contract", config.get("contract", default_contract), format_func=labels.get)
    row = contracts.loc[contract]
    strip = pd.period_range(row.delivery_start, row.delivery_end, freq="M")
    if len(strip) > 1:
        st.caption("This strip settles in monthly legs. Select a leg. The strip BFA cannot be compared directly with a monthly forecast.")
    leg = choice("Settlement month", list(map(str, strip)), state+"_leg", config.get("leg")) if len(strip) > 1 else str(strip[0])
    month = pd.Period(leg, "M")
    years_available = eligible_years(current_spot, issue)
    if not years_available:
        st.info("Complete historical Spot years are unavailable for this forecast date and data mode.")
        return
    window = choice("Reference window", WINDOWS, state+"_window", config.get("window", 20),
                    format_func=lambda w: f"{w} sessions" if isinstance(w, int) else w)
    custom = None
    if window == "Custom":
        saved = config.get("custom") or [str((issue-pd.Timedelta(days=28)).date()), str(issue.date())]
        dates = [min(issue.date(), max(spot.index[0].date(), pd.Timestamp(day).date())) for day in saved]
        start = calendar("Reference from", state+"_start", dates[0], spot.index[0].date(), issue.date())
        end = calendar("Reference to", state+"_end", dates[1], spot.index[0].date(), issue.date())
        if start > end:
            st.info("Select a valid reference range.")
            return
        custom = pd.Timestamp(start), pd.Timestamp(end)
    # Filter on the selected reference and target, so the five default years
    # are usable rather than merely the five most recent archive files.
    year_candidates = [year for year in years_available
                       if forecast(current_spot, issue, month, [year], window, custom)]
    defaults = [year for year in config.get("years", year_candidates[:5]) if year in year_candidates]
    years = multi("Historical years", year_candidates, state+"_years", defaults or year_candidates[:5], len(year_candidates)) if year_candidates else []
    if not years:
        st.info("No eligible analogues or the current settlement calendar has missing observations. Check the forecast date and reference window.")
        return
    value = forecast(current_spot, issue, month, years, window, custom)
    if value is None:
        st.info("The selected month lacks enough observations.")
        return
    bfa = row.price if len(strip) == 1 and row.quote_day == issue else np.nan
    if pd.isna(bfa):
        st.caption("A same-day BFA for this settlement leg is unavailable.")
    st.caption(f"{validation} · forecast as of {issue:%d %b %Y}.")
    st.caption("Historical actuals are reconstructed Spot averages. Clearinghouse settlement prints and older publication calendars remain unverified.")
    if not strict:
        st.caption("Archived publication times and revisions are unverified.")
    selected = choice("Base model", BASIC, state+"_base", config.get("base", "Difference"), control="segmented_control")
    factors, weights, method, custom_factor, custom_adjustment = [], [], "Manual", "", 0.
    sessions_left = value["left"]
    if sessions_left is None:
        st.caption("Future dry-index calendar unavailable. The forecast assumes a constant level for the whole month.")
    elif value["fixed"]:
        st.caption(f"{value['fixed']} observed settlement days fixed · {sessions_left} remaining.")
    if model == "Exploratory Model":
        method = choice("Adjustment", ["Manual", "Linear", "Ridge"], state+"_method", config.get("method", "Manual"),
                        control="segmented_control")
        chosen = multi("Factors", FACTORS, state+"_factors", config.get("factors", []), 4)
        factors = [FACTORS[name][1] for name in chosen]
        with st.popover("Factor settings"):
            for index, name in enumerate(chosen):
                guide, field = FACTORS[name]
                st.caption(f"{name}. {guide}")
                if method == "Manual":
                    saved_weights = config.get("weights", {})
                    weights.append(st.number_input(f"{name} weight", -5., 5., float(saved_weights.get(field, 0.)), .1,
                                                   key=state+"_weight_"+field))
            if method == "Manual":
                custom_factor = st.text_input("Custom factor name", value=config.get("custom_factor", ""), max_chars=80, key=state+"_custom")
                if custom_factor.strip():
                    custom_adjustment = st.number_input("Custom adjustment (USD/day)", value=float(config.get("custom_adjustment", 0.)),
                                                         step=100., key=state+"_custom_value")
                    st.caption("Custom observations are saved with this forecast. They are excluded from historical scoring.")
        if len(chosen) >= 3:
            st.caption("Use a small set of distinct factors. Momentum and strength can overlap.")
    settings = dict(contract=contract, leg=str(month), window=window, years=list(years), base=selected, method=method,
                    factors=[name for name, (_, field) in FACTORS.items() if field in factors], weights=dict(zip(factors, weights)),
                    custom_factor=custom_factor, custom_adjustment=custom_adjustment,
                    custom=[str(d.date()) for d in custom] if custom else None, validation=validation)
    windows = tuple(w for w in WINDOWS if w != "Custom" or custom)
    offsets = tuple(issue.year - year for year in years)
    scope = (vessel, benchmark, len(quotes))
    run_key = state + "_run"
    validation_key = (revision, scope, str(issue), str(month), windows, offsets, custom, strict)
    if st.button("Run validation", key=run_key):
        st.session_state[run_key+"_request"] = validation_key
    requested = st.session_state.get(run_key+"_request") == validation_key
    requires_fit = model == "Exploratory Model" and method != "Manual" and bool(factors)
    rows = pd.DataFrame()
    if requested or requires_fit:
        with st.spinner("Calculating matched historical forecasts…"):
            rows = _replay(revision, scope, issue, month, windows if requested else (window,), offsets, custom, strict, quotes)
    active = rows[rows.window.eq(str(window))].dropna(subset=[*BASIC, "Spot", "BFA", "Actual"]).sort_values("date") if not rows.empty else rows
    price = value["values"][selected]
    coefficients = None
    if model == "Exploratory Model":
        inputs = np.asarray([value["factors"][factor] for factor in factors])
        if requires_fit:
            train = active
            if strict and not train.empty:
                train = train[train.target_available.lt(issue.tz_localize("UTC") + pd.Timedelta(days=1))]
            fit = fit_adjustment(train, selected, factors, method) if not train.empty else None
            if fit is None or not np.isfinite(inputs).all():
                st.info("Estimated weights require more distinct, non-collinear training months. Try fewer factors or Manual adjustment.")
                price = np.nan
            else:
                center, scale, coef = fit
                price += ((inputs-center)/scale @ coef) * value["multiplier"]
                coefficients = "; ".join(f"{name}: {weight:+.2f}" for name, weight in zip(factors, coef/scale))
        elif np.isfinite(inputs).all():
            price += (inputs @ np.asarray(weights) + custom_adjustment) * value["multiplier"]
        else:
            price = np.nan
            st.info("Selected factors require more recent Spot observations.")
    def show_money(number):
        return f"{number:,.0f}" if pd.notna(number) else "—"
    a, b, c = st.columns(3)
    a.metric("Forecast Settlement", show_money(price))
    b.metric("Current BFA", show_money(bfa))
    c.metric("Forecast − BFA", show_money(price-bfa))
    st.caption(f"USD/day · {month} · {selected} base. A {value['A']:,.0f}, B {value['B']:,.0f}, C {value['C']:,.0f}.")
    st.caption("Historical years anchor the reference period. Cross-year target months follow the same year offset.")
    st.caption(f"C + (A − B) = {show_money(value['remaining']['Difference'])} · C × (A ÷ B) = "
               f"{show_money(value['remaining']['Ratio'])}. Formula estimates apply to remaining sessions; observed sessions keep their actual value.")
    if model == "Exploratory Model":
        st.caption("Manual weights are subjective." if method == "Manual" else "Fitted coefficients use only matured labels.")
        if custom_factor.strip():
            st.caption("The custom adjustment is recorded prospectively. Historical scores below evaluate only factors with historical observations.")
        if coefficients:
            st.caption(coefficients)
    if not synthetic:
        _log(journal, settings, month, issue, price, bfa, model, latest, revision)
    if requested:
        _validation(rows, active, model, selected, factors, method, weights, strict, price-bfa, state)


def _validation(rows, active, model, selected, factors, method, weights, strict, gap, key):
    if active.empty:
        st.info("No matched historical observations. In verified mode the archive must include usable quote vintages.")
        return
    models = ["Difference", "Ratio", "Spot", "BFA"]
    if model == "Exploratory Model" and factors:
        active = exploratory_replay(active, selected, factors, method, weights, strict)
        models.append("Exploratory")
    # A fixed recent chronological holdout. Manual weights can be changed by
    # users after seeing history, so manual scores remain exploratory.
    dates = sorted(active.date.unique())
    cutoff = dates[int(len(dates)*.7)]
    test = active.loc[active.date.ge(cutoff)].dropna(subset=[*models, "Actual"])
    if test.delivery.nunique() < MIN_TEST:
        st.info(f"{len(test)} matched holdout cases. At least {MIN_TEST} are needed to report validation metrics.")
        return
    stats = metrics(test, models)
    focus = "Exploratory" if "Exploratory" in models else selected
    summary = stats[stats.model.eq(focus)].iloc[0]
    st.markdown(f"**{focus}** · MAE {summary.mae:,.0f} USD/day · bias {summary.bias:+,.0f} · "
                f"{summary.n} holdout months · {summary.improvement:+.1%} error improvement vs BFA.")
    with st.popover("Validation details"):
        st.caption(f"Out-of-sample R² {summary.r2:.2f}. Bias is forecast minus actual. Positive values indicate overestimation.")
        st.caption("Actual values are reconstructed from historical Spot and are not independently reconciled to clearinghouse prints. "
                   "Historical calendars are verified where available. Date replay cannot verify archived vintages. "
                   "The recent 30% is scored chronologically; exploratory coefficients refit using earlier matured targets only.")
        base_mae = stats[stats.model.eq(selected)].mae.iloc[0]
        if focus == "Exploratory":
            st.caption(f"Same-case MAE change vs base: {base_mae-summary.mae:+,.0f} USD/day. Fixed manual choices are a scenario; do not tune them on this holdout.")
    if pd.notna(gap):
        st.caption(f"Current absolute gap is {abs(gap)/max(summary.mae, .01):.1f}× historical MAE. This is a scale comparison, not a significance test.")
    view = choice("Validation chart", VALIDATION_VIEWS, key+"_chart")
    fig = chart("USD/day", height=365)
    if view == "Model Comparison":
        fig.add_bar(x=stats.model, y=stats.mae, marker_color=COLORS[:len(stats)])
    elif view == "Reference Window Comparison":
        same_cases = rows.loc[rows.date.lt(cutoff)].dropna(subset=["Actual", *BASIC])
        cases = same_cases.groupby("delivery").window.nunique()
        common = cases[cases.eq(rows.window.nunique())].index
        same_cases = same_cases[same_cases.delivery.isin(common)]
        if len(common) < MIN_TEST:
            st.info("Too few common holdout months across all reference windows.")
            return
        for name, color in zip(BASIC, COLORS):
            error = (same_cases[name]-same_cases.Actual).abs().groupby(same_cases.window).mean().reindex([str(w) for w in WINDOWS]).dropna()
            fig.add_bar(name=name, x=error.index, y=error, marker_color=color)
        fig.update_layout(barmode="group")
        st.caption(f"Development-period MAE across the same {len(common)} months. The recent holdout is reserved for model scoring.")
    else:
        labels = (focus, "BFA", "Actual") if view == "Forecast vs Actual" else (focus, "BFA")
        for name, color in zip(labels, COLORS):
            series = test[name] if view == "Forecast vs Actual" else test[name]-test.Actual
            fig.add_scatter(x=test.delivery, y=series, name=name, mode="lines+markers", line_color=color)
        date_axis(fig, (test.delivery.min(), test.delivery.max()))
        if view == "Forecast Error":
            fig.add_hline(y=0, line_dash="dot")
    display(fig, key+"_validation")
