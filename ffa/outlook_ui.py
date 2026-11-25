"""One future-facing chart, immutable issued paths, and compact mean checks."""
from hashlib import sha256
import json

import numpy as np
import pandas as pd
import streamlit as st

from .analytics import delivery_label
from .forecast_store import activate_configuration, read_issue_dates, read_records, save_records
from .outlook import (DEFAULTS, WINDOWS, apply_judgement, contract_history, evaluated_record,
                      forecast_path, independent_errors, prediction_record, validation_errors)
from .preferences import calendar, choice, encode, multi, toggle
from .settlement_ui import _trajectory_axis
from .store import connect
from .ui import COLORS, chart, display


@st.cache_data(max_entries=12, show_spinner=False)
def _path(stamp, contract, window, weighting, years, _fixed):
    config = dict(window=window,weighting=weighting)
    if years:
        config["years"] = list(years)
    return forecast_path(_fixed,contract,config)


@st.cache_data(ttl=120, max_entries=12, show_spinner=False)
def _saved(repo, credential_id, contract, day, _token):
    with connect(repo, _token) as storage:
        return read_records(storage, contract, day=day)


@st.cache_data(ttl=120, max_entries=12, show_spinner=False)
def _issued_days(repo, credential_id, contract, _token):
    with connect(repo, _token) as storage:
        return read_issue_dates(storage, contract)


@st.cache_data(max_entries=12, show_spinner=False)
def _errors(stamp, contract, window, weighting, horizons, years, _fixed):
    config = {**DEFAULTS, "window": window, "weighting": weighting,"years":list(years)}
    return validation_errors(_fixed, contract, config, horizons)


def _memory(key):
    identity = sha256((key+"_issued_paths").encode()).hexdigest()[:12]
    return st.session_state.setdefault("_ffa_preferences", {}).setdefault(identity, {})


def _records(key, contract, repo, credential_id, token, day=None):
    result = {identity: row for identity, row in _memory(key).items() if row.get("contract") == contract and
              (day is None or row.get("issued_on") == day)}
    if repo and token:
        try:
            result.update(_saved(repo, credential_id, contract, day, token))
        except Exception:
            st.caption("The private forecast journal is temporarily unavailable. Browser records remain available.")
    return result


def outlook_view(fixed, stamp, vessel, product, key, *, synthetic=False, repo=None, credential_id=None, token=None):
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    fixed = fixed[fixed.vessel.eq(vessel) & fixed["product"].eq(product) & fixed.currency.eq("USD") & fixed.unit.eq("day")]
    fixed = fixed[fixed.quote_day.le(today)]
    forwards = fixed[fixed.kind.eq("BFA") & fixed.delivery_start.notna() & fixed.delivery_end.notna() & fixed.price.notna()]
    if forwards.empty:
        st.info("Fixed BFA quotes in USD/day are required.")
        return
    with st.popover("Path settings"):
        expired = toggle("Include expired contracts", key+"_expired")
        weighting = choice("Mean weighting", ["Equal weighted", "Recent weighted"], key+"_weighting")
        show_baseline = toggle("Show unchanged-price baseline", key+"_baseline")
        st.caption("Forecast(t) = historical matching-contract price(t) + current reference mean − historical reference mean. Historical curves retain their seasonal shape; no trend or regression is fitted.")
    latest = forwards.quote_day.max()
    contracts = forwards.sort_values("quote_day").drop_duplicates("identity", keep="last")
    if not expired:
        contracts = contracts[contracts.delivery_end.gt(max(latest, today))]
    contracts = contracts.sort_values(["delivery_start", "tenor"])
    if contracts.empty:
        st.info("No contract has a remaining future quotation period. Enable Include expired contracts to review saved forecasts.")
        return
    labels = {row.identity: f"{delivery_label(row.tenor,row.delivery_start)} · {row.tenor}" for row in contracts.itertuples()}
    upcoming = contracts[contracts.tenor.eq("month") & contracts.delivery_start.gt(latest)]
    counts = forwards.groupby("identity").price.count()
    ready = upcoming[upcoming.identity.map(counts).ge(20)]
    if not ready.empty:
        upcoming = ready
    preferred = upcoming.identity.iloc[0] if not upcoming.empty else contracts.identity.iloc[0]
    contract = choice("Target contract", labels, key+"_contract", preferred, format_func=labels.get)
    state = key+"_"+sha256(contract.encode()).hexdigest()[:10]
    view = choice("Forecast view", ["Future path", "Saved forecast review", "Reference window comparison"], key+"_view",
                  control="segmented_control")
    if view == "Saved forecast review":
        dates = {row["issued_on"] for row in _memory(key).values() if row.get("contract") == contract}
        if repo and token:
            try:
                dates.update(_issued_days(repo, credential_id, contract, token))
            except Exception:
                st.caption("Private issue-date index is temporarily unavailable.")
        if not dates:
            st.info("No issued forecasts are saved for this contract yet. Historical forecasts are never fabricated.")
            return
        days = sorted(pd.Timestamp(day).date() for day in dates)
        issued = calendar("Issued on (UTC)", state+"_issued_on", days[-1], days[0], days[-1])
        _review(fixed, contract, state, _records(key, contract, repo, credential_id, token, str(issued)))
        return
    window = choice("Reference window", WINDOWS, state+"_window", 20,
                    format_func=lambda n: f"{n} valid quotes", control="segmented_control")
    with st.spinner("Preparing the remaining contract period…"):
        path, info = _path((stamp,len(fixed)),contract,window,weighting,(),fixed)
    if path.empty:
        st.info(info.get("reason", "No future indicative assessment dates remain for this contract."))
        return
    years = multi("Historical reference years",info["candidate_years"],state+"_years",info["reference_years"],len(info["candidate_years"]))
    if not years:
        st.info("Select at least one historical reference year.")
        return
    if years != info["reference_years"]:
        path, info = _path((stamp,len(fixed)),contract,window,weighting,tuple(years),fixed)
    if view == "Reference window comparison":
        _window_comparison(fixed,stamp,contract,weighting,tuple(years),state)
        return
    model = choice("BFA path",["Seasonal difference","Exploratory scenario"],state+"_model",control="segmented_control")
    path = path.copy()
    future = path[path.target_date.gt(today)]
    if future.empty:
        st.info("No future target dates remain. Use Saved forecast review to compare an issued path with actual quotes.")
        return
    config = {**DEFAULTS,"window":window,"weighting":weighting,"years":list(years)}
    if model == "Exploratory scenario":
        config["judgement"] = choice("Market judgement", ["Neutral", "Bearish", "Bullish"], state+"_judgement", control="segmented_control")
        config["strength"] = st.number_input("Judgement strength (0–1)", 0., 1., .3, .1, key=state+"_strength")
        config["reason"] = st.text_input("Reason (optional)", key=state+"_reason", max_chars=240)
        with st.popover("Judgement settings"):
            config["maximum_pct"] = st.number_input("Maximum adjustment (%)", 0., 100., 10., 1., key=state+"_maximum")
            duration = choice("Impact duration", ["Fade over 20 sessions", "Until expiry", "Custom date"], state+"_duration")
            end = future.target_date.iloc[min(19,len(future)-1)].date()
            if duration == "Custom date":
                end = calendar("Judgement end", state+"_judgement_end", end, today.date(), info["expiry"].date())
            elif duration == "Until expiry":
                end = info["expiry"].date()
            config.update(judgement_from=str(today.date()), judgement_until=str(end), fade=duration != "Until expiry")
        sign = {"Neutral": 0, "Bearish": -1, "Bullish": 1}[config["judgement"]]
        st.caption(f"Subjective maximum impact: {sign*config['strength']*config['maximum_pct']:+.1f}% · active through {config['judgement_until']}. This is a scenario assumption, not a probability.")
    path["scenario"] = apply_judgement(path, config)
    future = path[path.target_date.gt(today)]
    rows, _ = contract_history(fixed, contract)
    revision = info["source_revision"]
    record = prediction_record(path, info, contract, config, revision)
    if not synthetic and record:
        record = _memory(key).setdefault(record["id"], encode(record))
    mode = choice("Chart mode", ["Price path", "Difference vs current BFA"], state+"_chart_mode", control="segmented_control")
    overlays = []
    with st.popover("Previous forecasts"):
        load = toggle("Load saved versions", state+"_load")
        if load:
            saved = _records(key, contract, repo, credential_id, token)
            names = {identity: f"{row['created_at'][:16]} UTC · {row['config']['window']} quotes · {row['config'].get('judgement','Neutral')}" for identity,row in saved.items()}
            chosen = multi("Overlay versions", list(names), state+"_overlays", [], 3, format_func=names.get)
            overlays = [saved[identity] for identity in chosen]
    _future_chart(rows, future, info, mode, model, show_baseline, overlays, state)
    valid = future.dropna(subset=["scenario"])
    if valid.empty:
        st.info("The selected years have no matched future quotes after today.")
        return
    last = valid.iloc[-1]
    gap = last.scenario-info["latest"]
    direction = "above" if gap > 1 else "below" if gap < -1 else "near"
    st.markdown(f"**Last projected quote: {last.scenario:,.0f} USD/day · {direction} current BFA by {gap:+,.0f}**")
    st.caption(f"Forecast(t) = historical price(t) + A − B. A: current {window}-quote mean {info['level']:,.0f}; B: historical reference mean {info['historical_reference']:,.0f}. Years: {', '.join(map(str,years))}. Data as of {info['asof']:%d %b %Y}; contract expires {info['expiry']:%d %b %Y}.")
    st.caption("Historical fixed contracts match vessel, benchmark, tenor, delivery season and months to delivery. Reference dates shift by whole years; weekend/holiday alignment uses a real quote within 3 calendar days in the same month. Each point uses the same available years for its historical price and reference mean; missing matches remain gaps.")
    if len(years)<3:
        st.caption(f"Only {len(years)} completed matching historical paths are available. This is an exploratory seasonal scenario, not validated predictive evidence.")
    st.caption("Future dates use the dry-index publication calendar where available and weekdays otherwise; the forward-assessment schedule is indicative. No missing actual quote is filled. Long-horizon forecast skill is unestablished until validated.")
    if (today-info["asof"]).days > 4:
        st.warning("The latest input is more than four calendar days old. This projection uses stale inputs; it is not a fresh market assessment.")
    if not synthetic and record:
        with st.popover("Forecast records"):
            st.download_button("Export browser records", json.dumps(_memory(key), indent=2), "bfa-path-records.json", "application/json", key=state+"_export")
            writer = st.secrets.get("github_forecast_writer_token")
            if writer and repo:
                if st.button("Save and enable daily updates", key=state+"_save"):
                    try:
                        with connect(repo, writer) as storage:
                            save_records(storage, [record])
                            activate_configuration(storage, record)
                        _saved.clear()
                        _issued_days.clear()
                        st.success("Saved to the private journal. The scheduled worker will reuse this configuration; judgement dates remain fixed.")
                    except Exception:
                        st.error("The private journal could not be updated. The browser record is retained; try again.")
            else:
                st.caption("Interactive records are saved in this browser. An optional private forecast writer token enables cloud saving and daily updates for your chosen configuration. The scheduled worker saves the default seasonal-difference model independently.")


def _future_chart(rows, future, info, mode, model, baseline, overlays, key):
    fig = chart("USD/day", height=440)
    difference = mode != "Price path"
    history = rows[rows.quote_day.ge(info["asof"]-pd.Timedelta(days=90))]
    offset = info["latest"] if difference else 0
    fig.add_scatter(x=history.quote_day, y=history.price-offset, name="Actual Spot" if info.get("target_kind")=="Spot" else "Actual BFA", mode="lines", line_color=COLORS[3])
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    connect_current = info["asof"].normalize() == today
    x = [info["asof"], *future.target_date] if connect_current else list(future.target_date)
    for column, name, color in [("forecast",info.get("config",{}).get("model","Seasonal forecast")+" forecast",COLORS[0]),("scenario","Exploratory scenario",COLORS[1])]:
        if column == "scenario" and model != "Exploratory scenario":
            continue
        y = [info["latest"]-offset, *(future[column]-offset)] if connect_current else future[column]-offset
        details = future[["historical_mean","historical_reference","historical_n"]].to_numpy()
        if connect_current:
            details = np.vstack([[np.nan,np.nan,0],details])
        memberships=list(future.get("historical_years",pd.Series("",index=future.index)))
        if connect_current:memberships=["",*memberships]
        xx,yy,dd=[],[],[]
        for i,(date,value,detail) in enumerate(zip(x,y,details)):
            if i and memberships[i]!=memberships[i-1]:
                xx.append(None);yy.append(None);dd.append([None,None,None,""])
            xx.append(date);yy.append(value);dd.append([*detail,memberships[i]])
        fig.add_scatter(x=xx,y=yy,name=name,mode="lines+markers",marker_size=3,
                        line=dict(color=color,dash="dash"),connectgaps=False,
                        customdata=dd,
                        hovertemplate="%{x|%d %b %Y}<br>%{y:,.0f} USD/day<br>Historical price %{customdata[0]:,.0f}<br>Historical reference %{customdata[1]:,.0f}<br>Matched years %{customdata[2]:.0f}: %{customdata[3]}<extra>%{fullData.name}</extra>")
    if baseline:
        fig.add_scatter(x=future.target_date, y=future.unchanged-offset, name="Unchanged baseline", mode="lines", line=dict(color=COLORS[4], dash="dot"))
    for record in overlays:
        points = pd.DataFrame(record["points"])
        fig.add_scatter(x=pd.to_datetime(points.target_date), y=points.scenario-offset,
                        name=f"Issued {record['issued_on']}", mode="lines", line=dict(dash="dot", width=1.5))
    fig.add_vrect(x0=future.target_date.min(), x1=info["expiry"], fillcolor="#177b81", opacity=.07, line_width=0)
    fig.add_shape(type="line", x0=info["asof"], x1=info["asof"], y0=0, y1=1, yref="paper", line=dict(color="#7a8799", dash="dot"))
    fig.add_shape(type="line", x0=info["expiry"], x1=info["expiry"], y0=0, y1=1, yref="paper", line=dict(color="#7a8799", dash="dot"))
    fig.add_annotation(x=info["expiry"], y=1, yref="paper", text="Contract expiry", showarrow=False,
                       xanchor="right", yanchor="bottom", font=dict(size=10,color="#516478"))
    if difference:
        fig.add_hline(y=0, line_dash="dot")
    dates = pd.DataFrame({"date": [history.quote_day.min(), info["expiry"]]})
    _trajectory_axis(fig, dates)
    display(fig, key+"_future_path")


def _review(fixed, contract, key, saved):
    if not saved:
        st.info("No issued forecasts are saved for this contract yet. New paths are recorded prospectively; historical forecasts are never fabricated.")
        return
    choices = sorted(saved, key=lambda identity: saved[identity]["created_at"], reverse=True)
    selected = choice("Issued forecast", choices, key+"_issued", format_func=lambda identity: f"{saved[identity]['created_at'][:16]} UTC · {saved[identity]['config']['window']} quotes · {identity[:6]}")
    record = saved[selected]
    points = evaluated_record(record, fixed)
    mode = choice("Chart mode", ["Forecast vs actual", "Forecast error"], key+"_review_mode", control="segmented_control")
    fig = chart("USD/day", height=440)
    if mode == "Forecast error":
        fig.add_scatter(x=points.target_date, y=points.error, name="Issued forecast − actual", mode="lines+markers", line_color=COLORS[0])
        fig.add_hline(y=0, line_dash="dot")
    else:
        for column, label, dash, color in [("scenario", "Issued forecast", "dash", COLORS[0]), ("actual", "Actual BFA", "solid", COLORS[3])]:
            fig.add_scatter(x=points.target_date, y=points[column], name=label, mode="lines+markers", line=dict(dash=dash, color=color), marker_size=3, connectgaps=False)
    _trajectory_axis(fig, points.rename(columns={"target_date":"date"}))
    display(fig, key+"_review")
    matched = points.dropna(subset=["actual"])
    if matched.empty:
        st.caption("Actual target-date quotes have not arrived yet. The issued path stays unchanged while outcomes accumulate.")
    else:
        hits = (np.sign(matched.scenario-record["anchor"]) == np.sign(matched.actual-record["anchor"])).mean()
        st.caption(f"{len(matched)} matched dates · MAE {matched.error.abs().mean():,.0f} USD/day · direction agreement {hits:.1%}. Points from one issued path overlap and are not independent test cases.")
    st.caption(f"Generated {record['created_at']} · inputs through {record['asof']} · method {record['engine']} · immutable version {record['id']}. Actuals are joined separately; subsequent parameter changes never rewrite this issued forecast.")


def _window_comparison(fixed,stamp,contract,weighting,years,key):
    horizon = choice("Validation horizon", [1, 5, 20, 60], key+"_validation_h", 20, control="segmented_control", format_func=lambda h:f"{h} sessions")
    errors = {}
    with st.spinner("Comparing means on common historical cases…"):
        for window in WINDOWS:
            rows = _errors((stamp,len(fixed)),contract,window,weighting,(horizon,),years,fixed)
            if not rows.empty:
                errors[window] = rows
    if not errors:
        st.info("No matured historical cases for this tenor and exact months to delivery.")
        return
    common = None
    for rows in errors.values():
        cases = set(zip(rows.date, rows.target_date, rows.contract))
        common = cases if common is None else common & cases
    scored = []
    for window, rows in errors.items():
        matched = rows[[case in common for case in zip(rows.date, rows.target_date, rows.contract)]]
        if matched.empty:
            continue
        cutoff = sorted(matched.date.unique())[int(matched.date.nunique()*.7)]
        development = matched[matched.target_date.lt(cutoff)]
        cases = independent_errors(development, horizon)
        if len(cases) < 10:
            continue
        scored.append((window, (cases.forecast-cases.actual).abs().mean(), (cases.unchanged-cases.actual).abs().mean(), len(cases)))
    if not scored:
        st.info("Fewer than 10 common non-overlapping development cases. No window ranking is reported.")
        return
    fig = chart("Development MAE · USD/day", height=350)
    fig.add_scatter(x=[r[0] for r in scored],y=[r[1] for r in scored],name="Seasonal difference",mode="lines+markers",line_color=COLORS[0])
    fig.add_scatter(x=[r[0] for r in scored], y=[r[2] for r in scored], name="Unchanged price", mode="lines+markers", line_color=COLORS[3])
    fig.update_xaxes(title="Reference window · valid quotes", tickvals=list(WINDOWS))
    display(fig,key+"_windows")
    st.caption(f"Same {scored[0][3]} development cases per window, matched by tenor and exact months to delivery. Recent 30% is reserved and not used to rank windows; overlapping labels are removed. Archived publication times and revisions remain unverified. This comparison does not automatically select a winner.")
