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
from ffa.preferences import calendar, choice, multi, persist_preferences, restore_preferences, toggle
from ffa.refresh import refresh_conclusion, start_refresh
from ffa.store import connect, market
from ffa.correlation import correlation_view
from ffa.explorer import explorer
from ffa.ui import TENORS, curve_changes, curves, section
from ffa.spot import seasonality, spot_analysis
from ffa.workspace import MarketWorkspace
from ffa.predictive_ui import predictive_view
from ffa.forecast_ui import unified_forecast

st.set_page_config(page_title="Baltic FFA | Market Monitor", layout="wide", initial_sidebar_state="auto")

BENCHMARKS = {
    "CAPE": ("C5TC (182)", "C5TC"),
    "PMX": ("P5TC",),
    "SMX": ("S11TC", "S10TC"),
    "HDY": ("HS7TC", "H7TC"),
}

STYLE = """
<style>
[data-testid="stMainBlockContainer"] {max-width: 100%; padding: 2rem clamp(1.25rem, 3.25vw, 3.5rem) 4rem;}
@media (min-width: 768px) {
    [data-testid="stSidebar"][aria-expanded="true"] { width: 216px !important; min-width: 216px !important; }
}
[data-testid="stPlotlyChart"] {background:#fff; border:0; border-radius:13px;
    padding:0; box-shadow:0 0 0 1px #dfe7ef, 0 2px 10px rgba(19, 41, 65, .04);}
/* Keep Plotly's native double-ended drag behaviour without a miniature chart. */
[data-testid="stPlotlyChart"] .rangeslider-rangeplot {visibility:hidden;}
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
    with st.popover("Date range"):
        preset = choice("Range", periods, key + "_window", "3Y" if research else "3M", control="segmented_control")
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
        if st.button("Sync market data", help="Fetch the latest Baltic assessments through GitHub Actions."):
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
        st.button("Sync market data", disabled=True, key="refresh_in_progress")
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
        if live:
            dispatch_token = st.secrets.get("github_actions_token")
            if dispatch_token:
                refresh_control(st.secrets["github_repo"], dispatch_token)
            else:
                st.caption("Section Refresh buttons reload saved quotes.")
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
        source=module_workspace("forward").fixed
        observed=source[source.vessel.eq(vessel)&source["product"].eq(product)&source.kind.eq("BFA")&source.tenor.eq(tenor)]
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

    def module_workspace(module):
        saved=st.session_state.get("_panel_workspace_"+module)
        return saved[1] if saved and saved[0]==stamp else workspace

    def panel_refresh(module):
        counter="_panel_refresh_"+module
        if st.button("Refresh",key=module+"_local_refresh",help="Reload saved quotes and recompute this module only. No collection task is started."):
            try:
                with st.spinner("Reloading saved quotes…"):
                    if live:
                        read_live.clear(repo,credential_id,token)
                        fresh=read_live(repo,credential_id,token)
                    else:
                        fresh=read_demo()
                count=st.session_state.get(counter,0)+1
                st.session_state[counter]=count
                scoped=MarketWorkspace(fresh[0],fresh[1],(stamp,count),revision,max_views=12)
                st.session_state["_panel_workspace_"+module]=(stamp,scoped)
            except Exception:
                st.warning("Could not reload saved quotes. Current data and selections are retained.")
        return st.session_state.get(counter,0)

    def panel_last(module):
        rows=module_workspace(module).fixed
        rows=rows[rows.vessel.eq(vessel)&rows["product"].eq(product)]
        return rows.quote_day.max().date() if not rows.empty else last

    def inputs(start,end,*,wide=False,seasonal=False,module=None):
        source=module_workspace(module) if module else workspace
        return source.view(vessel,product,start,end,load_window,wide=wide,seasonal=seasonal)

    @st.fragment
    def spot_panel():
        section(2, "Spot Market", "Spot trends and seasonal comparisons.")
        key = f"{vessel}_spot_{product}"
        panel_refresh("spot")
        current_last=panel_last("spot")
        study = choice("Spot view", ["Trend", "Seasonality"], key + "_view", control="segmented_control")
        if study == "Trend":
            bounds = (first,current_last)
            if bounds[0] <= bounds[1]:
                view = inputs(*bounds,seasonal=True,module="spot")
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
                view = inputs(start,end,seasonal=True,module="spot")
                spot_series = {leg: source for leg, source in view.local.items() if leg[2] == "Spot"}
                seasonality(spot_series, view.days, years, key)
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
        view = inputs(min(targets),max(targets),wide=True,module="forward")
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
        view = inputs(min(*targets,latest-timedelta(days=lookback*2+20)),max(targets),module="forward")
        curve_changes(view.fixed, tenor, mode, [pd.Timestamp(day) for day in targets], lookback, key)
        persist_preferences(key)

    @st.fragment
    def forward_panel():
        section(3, "Forward Market", "Compare prices, spreads and forward curves.")
        key = f"{vessel}_forward_{product}"
        panel_refresh("forward")
        study = choice("Forward view", ["Price Explorer", "Forward Curve", "Repricing"], key+"_view",
                       control="segmented_control")
        if study == "Price Explorer":
            loader=lambda *args,**kwargs:inputs(*args,module="forward",**kwargs)
            explorer(loader,(pd.Timestamp(first),pd.Timestamp(panel_last("forward"))),first,panel_last("forward"),key)
        elif study == "Forward Curve":
            curve_panel()
        else:
            changes_panel()
        persist_preferences(key)

    @st.fragment
    def forecast_panel():
        section(1, "Forecast", "Spot and fixed-contract BFA forecasts, replay and prospective review.")
        refresh_version=panel_refresh("forecast")
        current_last=panel_last("forecast")
        target=choice("Forecast target",["Spot Forecast","BFA Forecast"],f"{vessel}_{product}_forecast_target",control="segmented_control")
        kind="Spot" if target=="Spot Forecast" else "BFA"
        key=f"{vessel}_bfa_path_{product}" if kind=="BFA" else f"{vessel}_spot_forecast_{product}"
        start=first
        with st.spinner("Loading forecast data…"):
            view=inputs(start,current_last,seasonal=True,module="forecast")
        unified_forecast(view.fixed,(stamp,refresh_version),vessel,product,key,kind,synthetic=not live,repo=repo,credential_id=credential_id,token=token)
        persist_preferences(key)

    @st.fragment
    def research_panel():
        section(4, "Correlation", "Market relationships, seasonal analogies and potential leading signals.")
        key = f"{vessel}_correlation_{product}"
        refresh_version=panel_refresh("correlation")
        current_last=panel_last("correlation")
        loader=lambda start,end,wide=False:inputs(global_first if wide else start,end,wide=wide,module="correlation")
        correlation_view(loader,first,current_last,key)
        with st.expander('Existing signal validation'):
            if toggle('Show Spot/BFA out-of-sample validation',key+'_backtest'):
                start=max(first,current_last-timedelta(days=365*5))
                view=inputs(max(first,start-timedelta(days=730)),current_last,module='correlation')
                st.caption('Retained Spot/BFA walk-forward research; separate from correlations of the selected factors. No new regression or PCA model is added.')
                predictive_view(view.fixed,(pd.Timestamp(start),pd.Timestamp(current_last)),key+'_predictive','Forecast backtest',(stamp,start,current_last,refresh_version))
        persist_preferences(key)

    forecast_panel()
    spot_panel()
    forward_panel()
    research_panel()
    persist_preferences("global")


if __name__ == "__main__":
    main()
