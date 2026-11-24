import hashlib
import html
import logging
import time
from datetime import timedelta

import pandas as pd
import streamlit as st

from ffa.analytics import frame
from ffa.demo import data as demo_data
from ffa.history import read_history, read_manifest
from ffa.model import VESSELS
from ffa.preferences import calendar, choice, multi, persist_preferences, restore_preferences
from ffa.refresh import refresh_conclusion, start_refresh
from ffa.store import connect, market
from ffa.research import comparison_studies, historical_context
from ffa.explorer import explorer
from ffa.settlement_ui import settlement_view
from ffa.ui import TENORS, curve_changes, curves, section
from ffa.spot import seasonality, spot_analysis
from ffa.workspace import MarketWorkspace
from ffa.predictive_ui import predictive_view

st.set_page_config(page_title="Baltic FFA | Market Monitor", layout="wide", initial_sidebar_state="auto")

BENCHMARKS = {
    "CAPE": ("C5TC (182)", "C5TC"),
    "PMX": ("P5TC",),
    "SMX": ("S11TC", "S10TC"),
    "HDY": ("HS7TC", "H7TC"),
}

STYLE = """
<style>
[data-testid="stMainBlockContainer"] {max-width: 1360px; padding: 2rem clamp(1.25rem, 3.25vw, 3.5rem) 4rem;}
@media (min-width: 768px) {
    [data-testid="stSidebar"][aria-expanded="true"] { width: 216px !important; min-width: 216px !important; }
}
[data-testid="stPlotlyChart"] {background:#fff; border:0; border-radius:13px;
    padding:0; box-shadow:0 0 0 1px #dfe7ef, 0 2px 10px rgba(19, 41, 65, .04);}
[data-testid="stSidebarContent"] {border-right: 1px solid #e2e9ef;}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {color: #516478;}
[data-testid="stMetricValue"] {font-size:clamp(1rem,2.1vw,1.8rem);}
.ffa-hero {background: linear-gradient(112deg,#101c30,#17354a); border:1px solid #263e53;
    border-radius: 16px; color: #f6fbff; padding: 18px 26px; margin-bottom: .65rem;
    box-shadow: 0 8px 20px rgba(15, 34, 54, .12);}
.ffa-kicker {color:#a8d2d6; font:600 .73rem monospace; text-transform:uppercase; letter-spacing:.16em;}
.ffa-title {font-size:clamp(1.7rem, 2.7vw, 2.2rem); font-weight:730; letter-spacing:-.035em; line-height:1.15; margin:.25rem 0 .35rem; padding:0 !important;}
.ffa-meta {color:#d1e0ec; font-size:.91rem;}
.ffa-section {padding-top:1.15rem; border-top: 1px solid #dce5ed; margin-top:.4rem; scroll-margin-top:4rem;}
.ffa-section h2 {font-size:1.55rem; letter-spacing:-.025em; margin:0; padding:.1rem 0 .25rem;}
.ffa-section p {font-size:.89rem; color:#4f6478; margin:0 0 .2rem;}
.ffa-number {font:600 .73rem monospace; color:#216d72; letter-spacing:.1em; margin-right:.7rem;}
</style>
"""


@st.cache_data(ttl=120, show_spinner=False)
def read_live(repo, credential_id, _token):
    with connect(repo, _token) as storage:
        data, revision = storage.load()
        fixed, rolling, status = market(data)
        return frame(fixed), frame(rolling), status, revision


@st.cache_data(max_entries=2, show_spinner=False)
def read_demo():
    fixed, rolling, status = demo_data()
    return frame(fixed), frame(rolling), status, "demo-v1"


def default_product(data, vessel):
    preferred = BENCHMARKS[vessel]
    bfa = data[data.kind.eq("BFA")]
    if not bfa.empty:
        latest = bfa[bfa.quote_day.eq(bfa.quote_day.max())]
        for name in preferred:
            if name in set(latest["product"]):
                return name
        return sorted(latest["product"])[0]
    return data.sort_values("quote_day", ascending=False).iloc[0]["product"]


def assessment_window(first, last, key, research=False):
    periods = ({"1Y": 365, "3Y": 1095, "All": None, "Custom": None} if research else
               {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "All": None, "Custom": None})
    preset = choice("History", periods, key + "_window", "3Y" if research else "3M", control="segmented_control")
    if preset != "Custom":
        duration = periods[preset]
        return (max(first, last - timedelta(days=duration)) if duration else first, last)
    left, right = st.columns(2)
    start = calendar("From", key + "_start", max(first, last - timedelta(days=90)), first, last, container=left)
    end = calendar("To", key + "_end", last, first, last, container=right)
    return start, end


def show_status(status, vessel, live, fallback):
    row = next((item for item in status if item["vessel"] == vessel), {})
    state = {"ok": "Up to date", "partial": "Partial update", "failed": "Sync failed",
             "pending": "Awaiting sync"}.get(row.get("status"), "Awaiting sync" if live else "Demo")
    if fallback:
        state = "Using last loaded data"
    attempted = row.get("attempted_at")
    successful = row.get("succeeded_at")
    st.sidebar.markdown(f"**{state}**")
    if row.get("latest_day"):
        st.sidebar.caption(f"Latest assessment · {row['latest_day']}")
    if attempted:
        st.sidebar.caption(f"Last checked · {pd.Timestamp(attempted):%d %b %H:%M} UTC")
    if successful:
        st.sidebar.caption(f"Last synced · {pd.Timestamp(successful):%d %b %H:%M} UTC")


def refresh_control(repo, token):
    pending = st.session_state.get("_baltic_refresh")
    if pending is None:
        if st.button("Refresh", help="Fetch the latest Baltic assessments through GitHub Actions."):
            try:
                run = start_refresh(repo, token)
            except Exception as exc:
                logging.warning("Baltic refresh dispatch unavailable: %s", type(exc).__name__)
                st.warning("Could not start Baltic sync. Showing saved assessments.")
                return
            st.session_state["_baltic_refresh"] = run, time.monotonic()
            st.session_state.pop("_baltic_result", None)
            st.rerun()
        result = st.session_state.get("_baltic_result")
        if result:
            st.caption(result)
        return

    @st.fragment(run_every="15s")
    def progress():
        run, started = pending
        st.button("Refresh", disabled=True, key="refresh_in_progress")
        st.caption("Waiting for Baltic sync…")
        try:
            result = refresh_conclusion(repo, token, run)
        except Exception:
            result = None
        if result or time.monotonic() - started > 1800:
            st.session_state.pop("_baltic_refresh", None)
            st.session_state["_baltic_result"] = {
                "success": "Baltic sync finished. See the assessment date below.",
                None: "Baltic sync could not be confirmed. Showing saved assessments.",
            }.get(result, "Baltic sync reported a failure. Available assessments have been reloaded.")
            read_live.clear()
            st.rerun()

    progress()


def main():
    st.markdown(STYLE, unsafe_allow_html=True)
    restore_preferences()
    try:
        live = st.secrets.get("mode") == "live"
    except FileNotFoundError:
        live = False

    with st.sidebar:
        st.markdown("### Baltic FFA")
        st.caption("DRY FREIGHT RESEARCH")
        vessel = choice("Vessel class", VESSELS, "vessel", "PMX", control="radio", format_func=VESSELS.get)
        benchmark_area = st.container()
        navigation_area = st.container()
        if live:
            dispatch_token = st.secrets.get("github_actions_token")
            if dispatch_token:
                refresh_control(st.secrets["github_repo"], dispatch_token)
            else:
                st.button("Reload saved data", on_click=read_live.clear)
                st.caption("Baltic sync is not configured for this app.")

    archive = {}
    repo = credential_id = token = revision = data_revision = None
    fallback = False
    if live:
        try:
            token = st.secrets["github_reader_token"]
            repo = st.secrets["github_repo"]
            credential_id = hashlib.sha256(token.encode()).hexdigest()
            feed = read_live(repo, credential_id, token)
            fixed, rolling, status = feed[:3]
            data_revision = feed[3] if len(feed) > 3 else None
            st.session_state["_last_feed"] = feed
        except Exception as exc:
            logging.warning("FFA live feed unavailable: %s", type(exc).__name__)
            saved = st.session_state.get("_last_feed")
            if saved is None:
                st.error("Market data is temporarily unavailable.")
                return
            fixed, rolling, status = saved[:3]
            data_revision = saved[3] if len(saved) > 3 else None
            fallback = True
        try:
            revision, archive = read_manifest(repo, credential_id, token)
        except Exception as exc:
            logging.warning("FFA archive index unavailable: %s", type(exc).__name__)
            st.warning("Historical assessments are temporarily unavailable. Showing the latest feed.")
    else:
        fixed, rolling, status, data_revision = read_demo()
    show_status(status, vessel, live, fallback)

    if not isinstance(fixed, pd.DataFrame):
        fixed, rolling = frame(fixed), frame(rolling)
    market_data = fixed[fixed.vessel.eq(vessel)]
    if market_data.empty:
        st.title(VESSELS[vessel])
        st.info("No assessments are available for this vessel class.")
        return

    quote_day = market_data.quote_day.max()
    for kind in ("BFA", "Spot"):
        last = market_data.loc[market_data.kind.eq(kind), "quote_day"].max()
        if pd.notna(last):
            st.sidebar.caption(f"{kind} · {last:%d %b %Y}")
    source = "Baltic Exchange assessments" if live else "Synthetic demonstration data"
    products = sorted(market_data["product"].unique())
    preferred = default_product(market_data, vessel)
    product = choice("Benchmark", products, f"{vessel}_benchmark", preferred, container=benchmark_area)
    module = choice("Market view", ["Spot Market", "Forward Market", "Forecast", "Market Research"],
                    "main_market_view", container=navigation_area, control="radio")
    st.markdown(
        f'<div class="ffa-hero"><div class="ffa-kicker">Market monitor / Dry bulk</div>'
        f'<h1 class="ffa-title">{VESSELS[vessel]} · {html.escape(product)}</h1>'
        f'<div class="ffa-meta">{source} &nbsp;·&nbsp; Latest class quote {quote_day:%d %b %Y}</div></div>',
        unsafe_allow_html=True,
    )
    history_starts = archive.get("first_dates", {}).get(vessel, {})
    global_first = min(fixed.quote_day.min().date(),
                       *(pd.Timestamp(value).date() for value in history_starts.values())) if history_starts else fixed.quote_day.min().date()
    observed_days = market_data.loc[market_data["product"].eq(product), "quote_day"]
    first = min(observed_days.min().date(), pd.Timestamp(history_starts.get(product, observed_days.min())).date())
    last = observed_days.max().date()
    stamp = (repo, credential_id, data_revision, revision)
    workspace = st.session_state.get("_market_workspace")
    if not isinstance(workspace, MarketWorkspace) or workspace.revision != stamp or data_revision is None:
        workspace = MarketWorkspace(fixed, rolling, stamp, revision, max_views=12)
        st.session_state["_market_workspace"] = workspace

    def tenor_choice(key):
        present = market_data[market_data["product"].eq(product) & market_data.kind.eq("BFA")].tenor
        available = [tenor for tenor in TENORS if tenor in set(present)]
        return choice("Delivery", available, key, control="radio", horizontal=True,
                      format_func=TENORS.get) if available else None

    def latest_forward(tenor):
        observed = market_data[market_data["product"].eq(product) & market_data.kind.eq("BFA") & market_data.tenor.eq(tenor)]
        return observed.quote_day.max().date() if not observed.empty else last

    def load_window(start, end, scope_vessel, scope_product):
        if not archive:
            return frame([])
        warmup = (start - timedelta(days=60)).replace(month=1, day=1)
        years = tuple(year for year in archive["years"] if warmup.year <= int(year) <= end.year)
        if not years:
            return frame([])
        try:
            return read_history(repo, credential_id, revision, years, warmup, end, token,
                                vessel=scope_vessel, product=scope_product)
        except Exception:
            st.warning("Historical assessments are temporarily unavailable. Showing the latest feed.")
            missing = frame([])
            missing.attrs["history_unavailable"] = True
            return missing

    def inputs(start, end, *, wide=False, seasonal=False):
        return workspace.view(vessel, product, start, end, load_window, wide=wide, seasonal=seasonal)

    @st.fragment
    def spot_panel():
        section(1, "Spot Market", "Spot trends and seasonal comparisons.")
        key = f"{vessel}_spot_{product}"
        study = choice("Spot view", ["Trend", "Seasonality"], key + "_view", control="segmented_control")
        if study == "Trend":
            bounds = assessment_window(first, last, key)
            if bounds[0] <= bounds[1]:
                view = inputs(*bounds)
                spot_analysis(view.local, view.days, tuple(pd.Timestamp(day) for day in bounds), key)
            else:
                st.info("Choose a start date before the end date.")
        else:
            available = sorted({int(y) for y in archive.get("years", []) if first.year <= int(y) <= last.year}
                               | set(observed_days.dt.year), reverse=True)
            years = multi("Compare years", available, key+"_years", available[:5], len(available))
            if years:
                start = max(first, pd.Timestamp(min(years), 1, 1).date())
                end = min(last, pd.Timestamp(max(years), 12, 31).date())
                view = inputs(start, end, seasonal=True)
                seasonality(view.local, view.days, years, key, spot_only=True)
        persist_preferences(key)

    @st.fragment
    def curve_panel():
        st.markdown("**Forward curve · delivery snapshot**")
        key = f"{vessel}_curve_{product}"
        tenor = tenor_choice(key + "_tenor")
        latest = latest_forward(tenor)
        offset = choice("Snapshots", ["7D", "30D", "Custom"], key + "_snapshots", control="segmented_control")
        targets = [latest, latest - timedelta(days=7 if offset == "7D" else 30)]
        if offset == "Custom":
            with st.popover("Assessment dates"):
                count = choice("Snapshots", [1, 2, 3, 4], key + "_count", 2, control="segmented_control")
                targets = [calendar(f"Assessment {i + 1}", key + f"_date_{i}",
                                    max(global_first, latest - timedelta(days=7 * i)), global_first, latest)
                           for i in range(count)]
        view = inputs(min(targets), max(targets), wide=True)
        current = view.fixed[view.fixed.vessel.eq(vessel) & view.fixed["product"].eq(product)]
        curves(current, view.fixed, tenor, [pd.Timestamp(day) for day in targets], key)
        persist_preferences(key)

    @st.fragment
    def changes_panel():
        st.markdown("**Curve changes · matched deliveries**")
        key = f"{vessel}_changes_{product}"
        tenor = tenor_choice(key + "_tenor")
        latest = latest_forward(tenor)
        mode = choice("Analysis", ["Repricing", "Heatmap"], key + "_view", control="segmented_control",
                      label_visibility="collapsed")
        targets, lookback = [latest, latest - timedelta(days=7)], 20
        if mode == "Heatmap":
            lookback = choice("Assessments", [20, 60, 120], key + "_lookback", control="segmented_control",
                              format_func=lambda count: f"{count}D")
        else:
            with st.popover("Repricing dates"):
                custom = choice("Dates", ["Latest vs 7D ago", "Custom"], key + "_dates", control="segmented_control")
                if custom == "Custom":
                    targets = [calendar("To", key + "_to", latest, global_first, latest),
                               calendar("From", key + "_from", max(global_first, targets[1]), global_first, latest)]
        view = inputs(min(*targets, latest - timedelta(days=lookback * 2 + 20)), max(targets))
        curve_changes(view.fixed, tenor, mode, [pd.Timestamp(day) for day in targets], lookback, key)
        persist_preferences(key)

    @st.fragment
    def forward_panel():
        section(2, "Forward Market", "Compare prices, spreads and forward curves.")
        key = f"{vessel}_forward_{product}"
        study = choice("Forward view", ["Price Explorer", "Forward Curve", "Repricing"], key+"_view",
                       control="segmented_control")
        if study == "Price Explorer":
            bounds = assessment_window(first, last, key)
            if bounds[0] <= bounds[1]:
                explorer(inputs, tuple(pd.Timestamp(day) for day in bounds), first, last, key)
            else:
                st.info("Choose a start date before the end date.")
        elif study == "Forward Curve":
            curve_panel()
        else:
            changes_panel()
        persist_preferences(key)

    @st.fragment
    def forecast_panel():
        section(3, "Forecast", "Estimate settlement and compare it with a matching BFA.")
        key = f"{vessel}_settlement_{product}"
        # Load enough history for five analogues and chronological validation.
        start = max(first, last.replace(year=last.year-12, month=1, day=1))
        with st.spinner("Loading settlement history…"):
            view = inputs(start, last, seasonal=True)
        settlement_view(view.fixed, stamp, vessel, product, key, synthetic=not live)
        persist_preferences(key)

    @st.fragment
    def research_panel():
        section(4, "Market Research", "Short-horizon BFA moves and market relationships.")
        key = f"{vessel}_research_{product}"
        study = choice("Research view", ["Daily changes", "Lead / lag", "Forecast backtest", "Relationships"],
                       key+"_view", control="segmented_control")
        long_history = study in ("Lead / lag", "Forecast backtest")
        bounds = assessment_window(first, last, key+study, research=long_history)
        if bounds[0] > bounds[1]:
            st.info("Choose a start date before the end date.")
        else:
            start = max(first, bounds[0] - timedelta(days=730)) if long_history else bounds[0]
            view = inputs(start, last if study != "Relationships" else bounds[1])
            timestamps = tuple(pd.Timestamp(day) for day in bounds)
            if study == "Relationships":
                basis, spreads = comparison_studies(view.local, view.days)
                historical_context(view.local, view.days, basis, spreads, timestamps, key)
            else:
                predictive_view(view.fixed, timestamps, key+"_predictive", study, (stamp, start, last))
        persist_preferences(key)

    if module == "Spot Market":
        spot_panel()
    elif module == "Forward Market":
        forward_panel()
    elif module == "Forecast":
        forecast_panel()
    else:
        research_panel()
    persist_preferences("global")


if __name__ == "__main__":
    main()
