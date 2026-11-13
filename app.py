import hashlib
import logging
import time
from datetime import timedelta

import pandas as pd
import streamlit as st

from ffa.analytics import frame, options
from ffa.demo import data as demo_data
from ffa.history import merge_history, read_history, read_manifest
from ffa.model import VESSELS
from ffa.preferences import calendar, choice, multi, persist_preferences, restore_preferences
from ffa.refresh import refresh_conclusion, start_refresh
from ffa.store import connect, market
from ffa.research import calendar_spreads, comparison_studies, comparison_workspace, historical_context, spot_comparison
from ffa.ui import TENORS, curve_changes, curves, prices, section
from ffa.spot import seasonality, spot_analysis

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
[data-testid="stPlotlyChart"] {background:#fff; border:1px solid #dfe7ef; border-radius:13px;
    padding:.35rem .5rem; box-shadow:0 2px 10px rgba(19, 41, 65, .04);}
[data-testid="stSidebarContent"] {border-right: 1px solid #e2e9ef;}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {color: #516478;}
[data-testid="stMetricValue"] {font-size:clamp(1rem,2.1vw,1.8rem);}
.ffa-hero {background: linear-gradient(112deg,#101c30,#17354a); border:1px solid #263e53;
    border-radius: 16px; color: #f6fbff; padding: clamp(20px, 4vw, 46px); margin-bottom: .65rem;
    box-shadow: 0 8px 20px rgba(15, 34, 54, .12);}
.ffa-kicker {color:#a8d2d6; font:600 .73rem monospace; text-transform:uppercase; letter-spacing:.16em;}
.ffa-title {font-size:clamp(2rem, 4vw, 3.6rem); font-weight:730; letter-spacing:-.045em; line-height:1.15; margin:.35rem 0 .55rem;}
.ffa-meta {color:#d1e0ec; font-size:.91rem;}
.ffa-section {padding-top:1.15rem; border-top: 1px solid #dce5ed; margin-top:.4rem;}
.ffa-section h2 {font-size:1.55rem; letter-spacing:-.025em; margin:0; padding:.1rem 0 .25rem;}
.ffa-section p {font-size:.89rem; color:#4f6478; margin:0 0 .2rem;}
.ffa-number {font:600 .73rem monospace; color:#216d72; letter-spacing:.1em; margin-right:.7rem;}
</style>
"""


@st.cache_data(ttl=120, show_spinner=False)
def read_live(repo, credential_id, _token):
    with connect(repo, _token) as storage:
        data, _ = storage.load()
        fixed, rolling, status = market(data)
        return frame(fixed), frame(rolling), status


@st.cache_data(max_entries=2, show_spinner=False)
def read_demo():
    fixed, rolling, status = demo_data()
    return frame(fixed), frame(rolling), status


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


def assessment_window(first, last, key):
    periods = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "All": None, "Custom": None}
    preset = choice("History", periods, key + "_window", "3M", control="segmented_control")
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
        if live:
            dispatch_token = st.secrets.get("github_actions_token")
            if dispatch_token:
                refresh_control(st.secrets["github_repo"], dispatch_token)
            else:
                st.button("Reload saved data", on_click=read_live.clear)
                st.caption("Baltic sync is not configured for this app.")

    archive = {}
    fallback = False
    if live:
        try:
            token = st.secrets["github_reader_token"]
            repo = st.secrets["github_repo"]
            credential_id = hashlib.sha256(token.encode()).hexdigest()
            fixed, rolling, status = read_live(repo, credential_id, token)
            st.session_state["_last_feed"] = fixed, rolling, status
        except Exception as exc:
            logging.warning("FFA live feed unavailable: %s", type(exc).__name__)
            saved = st.session_state.get("_last_feed")
            if saved is None:
                st.error("Market data is temporarily unavailable.")
                return
            fixed, rolling, status = saved
            fallback = True
        try:
            revision, archive = read_manifest(repo, credential_id, token)
        except Exception as exc:
            logging.warning("FFA archive index unavailable: %s", type(exc).__name__)
            st.warning("Historical assessments are temporarily unavailable. Showing the latest feed.")
    else:
        fixed, rolling, status = read_demo()
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
    st.markdown(
        f'<div class="ffa-hero"><div class="ffa-kicker">Market monitor / Dry bulk</div>'
        f'<h1 class="ffa-title">{VESSELS[vessel]}</h1>'
        f'<div class="ffa-meta">{source} &nbsp;·&nbsp; Latest class quote {quote_day:%d %b %Y}</div></div>',
        unsafe_allow_html=True,
    )
    products = sorted(market_data["product"].unique())
    preferred = default_product(market_data, vessel)
    history_starts = archive.get("first_dates", {}).get(vessel, {})
    global_first = min(fixed.quote_day.min().date(),
                       *(pd.Timestamp(value).date() for value in history_starts.values())) if history_starts else fixed.quote_day.min().date()

    def product_choice(key, container=st):
        return choice("Benchmark", products, key + "_product", preferred, container=container)

    def dates(product):
        days = market_data.loc[market_data["product"].eq(product), "quote_day"]
        first, last = days.min().date(), days.max().date()
        return min(first, pd.Timestamp(history_starts.get(product, first)).date()), last

    def tenor_choice(product, key):
        present = market_data[market_data["product"].eq(product) & market_data.kind.eq("BFA")].tenor
        available = [tenor for tenor in TENORS if tenor in set(present)]
        return choice("Delivery", available, key, control="radio", horizontal=True,
                      format_func=TENORS.get) if available else None

    def latest_forward(product, tenor):
        observed = market_data[market_data["product"].eq(product) & market_data.kind.eq("BFA") & market_data.tenor.eq(tenor)]
        return observed.quote_day.max().date() if not observed.empty else dates(product)[1]

    def load_window(start, end):
        if not archive:
            return fixed, rolling
        # Include the full warm-up year, not just 20 calendar days.
        warmup = (start - timedelta(days=60)).replace(month=1, day=1)
        years = tuple(year for year in archive["years"] if warmup.year <= int(year) <= end.year)
        if not years:
            return fixed, rolling
        try:
            history = read_history(repo, credential_id, revision, years, warmup, end, token)
            return merge_history(fixed, rolling, history)
        except Exception:
            st.warning("Historical assessments are temporarily unavailable. Showing the latest feed.")
            return fixed, rolling

    def inputs(product, start, end):
        complete, rolls = load_window(start, end)
        current = complete[complete.vessel.eq(vessel) & complete["product"].eq(product)]
        relative = rolls[rolls.vessel.eq(vessel) & rolls["product"].eq(product)]
        days = pd.DatetimeIndex(sorted(current.quote_day.unique()))
        return options(current, relative), days, current, complete, rolls

    @st.fragment
    def spot_panel():
        section(1, "Spot Analysis", "Spot Bollinger bands and separate MA5 / MA20 histories.")
        key = f"{vessel}_spot"
        product = product_choice(key)
        key += "_" + product
        bounds = assessment_window(*dates(product), key)
        if bounds[0] <= bounds[1]:
            local, days, *_ = inputs(product, *bounds)
            spot_analysis(local, days, tuple(pd.Timestamp(day) for day in bounds), key)
        else:
            st.info("Choose a start date before the end date.")
        persist_preferences(key)

    @st.fragment
    def price_panel():
        section(2, "Price Explorer", "Follow the benchmark, compare contracts, or build a two-leg spread.")
        key = f"{vessel}_price"
        product = product_choice(key)
        key += "_" + product
        mode = choice("Analysis", ["Spot + BFA", "Multi-series", "Spread / Ratio"], key + "_view",
                      control="segmented_control", label_visibility="collapsed")
        bounds = assessment_window(*dates(product), key + mode)
        if bounds[0] > bounds[1]:
            st.info("Choose a start date before the end date.")
        else:
            local, days, _, complete, rolls = inputs(product, *bounds)
            timestamps = tuple(pd.Timestamp(day) for day in bounds)
            if mode == "Spot + BFA":
                spot_comparison(local, days, timestamps, key)
            else:
                basis, calendar_studies = comparison_studies(local, days)
                comparison_workspace(options(complete, rolls), local, basis, calendar_studies,
                                     timestamps, key + mode, mode == "Spread / Ratio")
        persist_preferences(key)

    @st.fragment
    def curve_panel():
        section(3, "Forward Curve", "Compare assessments by delivery period.")
        key = f"{vessel}_curve"
        product = product_choice(key)
        key += "_" + product
        tenor = tenor_choice(product, key + "_tenor")
        latest = latest_forward(product, tenor)
        offset = choice("Snapshots", ["7D", "30D", "Custom"], key + "_snapshots", control="segmented_control")
        targets = [latest, latest - timedelta(days=7 if offset == "7D" else 30)]
        if offset == "Custom":
            with st.popover("Assessment dates"):
                count = choice("Snapshots", [1, 2, 3, 4], key + "_count", 2, control="segmented_control")
                targets = [calendar(f"Assessment {i + 1}", key + f"_date_{i}",
                                    max(global_first, latest - timedelta(days=7 * i)), global_first, latest)
                           for i in range(count)]
        _, _, current, complete, _ = inputs(product, min(targets), max(targets))
        curves(current, complete, tenor, [pd.Timestamp(day) for day in targets], key)
        persist_preferences(key)

    @st.fragment
    def changes_panel():
        section(4, "Curve Changes", "Compare the same fixed deliveries across assessment dates.")
        key = f"{vessel}_changes"
        product = product_choice(key)
        key += "_" + product
        tenor = tenor_choice(product, key + "_tenor")
        latest = latest_forward(product, tenor)
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
        _, _, current, *_ = inputs(product, min(*targets, latest - timedelta(days=lookback * 2 + 20)), max(targets))
        curve_changes(current, tenor, mode, [pd.Timestamp(day) for day in targets], lookback, key)
        persist_preferences(key)

    @st.fragment
    def term_panel():
        section(5, "Term Structure", "Fixed deliveries, rolling histories, calendar spreads and historical context.")
        key = f"{vessel}_term"
        product = product_choice(key)
        key += "_" + product
        mode = choice("Series", ["Fixed", "Rolling"], key + "_mode", control="segmented_control")
        bounds = assessment_window(*dates(product), key + mode)
        if bounds[0] > bounds[1]:
            st.info("Choose a start date before the end date.")
        else:
            local, days, *_ = inputs(product, *bounds)
            timestamps = tuple(pd.Timestamp(day) for day in bounds)
            if mode == "Rolling":
                st.caption("Unadjusted prices. Lines break at contract rolls; diamonds mark the roll date. MA5 / MA20 and Bollinger bands restart for each delivery contract.")
            prices(local, days, timestamps, key, mode)
            calendar_studies = calendar_spreads({leg: value for leg, value in local.items() if leg[6] == mode},
                                               days, timestamps, key + mode)
            basis, _ = comparison_studies(local, days)
            historical_context(local, days, basis, calendar_studies, timestamps, key)
        persist_preferences(key)

    @st.fragment
    def seasonal_panel():
        section(6, "Seasonality", "Compare Spot or the same rolling tenor across calendar years. Fixed deliveries stay in Term Structure.")
        key = f"{vessel}_seasonal"
        product = product_choice(key)
        key += "_" + product
        latest = dates(product)[1]
        available = sorted({int(year) for year in archive.get("years", []) if int(year) <= latest.year}
                           | set(market_data.loc[market_data["product"].eq(product), "quote_day"].dt.year), reverse=True)
        years = multi("Compare years", available, key + "_years", available[:5], len(available))
        if years:
            start, end = pd.Timestamp(min(years), 1, 1).date(), min(pd.Timestamp(max(years), 12, 31).date(), latest)
            local, days, *_ = inputs(product, start, end)
            seasonality(local, days, years, key)
        else:
            st.info("Select at least one year.")
        persist_preferences(key)

    spot_panel()
    price_panel()
    curve_panel()
    changes_panel()
    term_panel()
    seasonal_panel()


if __name__ == "__main__":
    main()
