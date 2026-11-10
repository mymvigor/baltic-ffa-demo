import hashlib
import logging
from datetime import timedelta

import pandas as pd
import streamlit as st

from ffa.analytics import frame, options
from ffa.demo import data as demo_data
from ffa.history import merge_history, read_history, read_manifest
from ffa.model import VESSELS
from ffa.preferences import calendar, choice
from ffa.store import connect, market
from ffa.research import calendar_spreads, comparison_studies, comparison_workspace, historical_context, spot_comparison
from ffa.ui import TENORS, curve_changes, curves, prices, section

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
        return market(data)


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


def main():
    st.markdown(STYLE, unsafe_allow_html=True)
    try:
        live = st.secrets.get("mode") == "live"
    except FileNotFoundError:
        live = False

    with st.sidebar:
        st.markdown("### Baltic FFA")
        st.caption("DRY FREIGHT RESEARCH")
        vessel = choice("Vessel class", VESSELS, "vessel", "PMX", control="radio", format_func=VESSELS.get)
        st.button("Refresh", on_click=read_live.clear, help="Reload the latest data saved by the collector.")

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
        fixed, rolling, status = demo_data()
    show_status(status, vessel, live, fallback)

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

    price_key, curve_key, changes_key, term_key = (f"{vessel}_{panel}" for panel in ("price", "curve", "changes", "term"))
    price_panel, curve_panel, changes_panel, term_panel = (st.container() for _ in range(4))

    with price_panel:
        section(1, "Price Explorer", "Follow the benchmark, compare contracts, or build a two-leg spread.")
        price_product = product_choice(price_key)
        price_key += "_" + price_product
        price_mode = choice("Analysis", ["Spot + BFA", "Multi-series", "Spread / Ratio"], price_key + "_view",
                            control="segmented_control", label_visibility="collapsed")
        price_bounds = assessment_window(*dates(price_product), price_key + price_mode)

    with curve_panel:
        section(2, "Forward Curve", "Compare assessments by delivery period.")
        curve_product = product_choice(curve_key)
        curve_key += "_" + curve_product
        curve_tenor = tenor_choice(curve_product, curve_key + "_tenor")
        curve_latest = latest_forward(curve_product, curve_tenor)
        offset = choice("Snapshots", ["7D", "30D", "Custom"], curve_key + "_snapshots",
                        control="segmented_control")
        curve_targets = [curve_latest, curve_latest - timedelta(days=7 if offset == "7D" else 30)]
        if offset == "Custom":
            with st.popover("Assessment dates"):
                count = choice("Snapshots", [1, 2, 3, 4], curve_key + "_count", 2, control="segmented_control")
                curve_targets = [
                    calendar(f"Assessment {i + 1}", curve_key + f"_date_{i}",
                             max(global_first, curve_latest - timedelta(days=7 * i)), global_first, curve_latest)
                    for i in range(count)
                ]

    with changes_panel:
        section(3, "Curve Changes", "Compare the same fixed deliveries across assessment dates.")
        changes_product = product_choice(changes_key)
        changes_key += "_" + changes_product
        changes_tenor = tenor_choice(changes_product, changes_key + "_tenor")
        changes_latest = latest_forward(changes_product, changes_tenor)
        change_mode = choice("Analysis", ["Repricing", "Heatmap"], changes_key + "_view",
                             control="segmented_control", label_visibility="collapsed")
        changes_targets = [changes_latest, changes_latest - timedelta(days=7)]
        lookback = 20
        if change_mode == "Heatmap":
            lookback = choice("Assessments", [20, 60, 120], changes_key + "_lookback", control="segmented_control",
                              format_func=lambda count: f"{count}D")
        else:
            with st.popover("Repricing dates"):
                custom = choice("Dates", ["Latest vs 7D ago", "Custom"], changes_key + "_dates", control="segmented_control")
                if custom == "Custom":
                    changes_targets = [
                        calendar("To", changes_key + "_to", changes_latest, global_first, changes_latest),
                        calendar("From", changes_key + "_from", max(global_first, changes_targets[1]), global_first, changes_latest),
                    ]

    with term_panel:
        section(4, "Term Structure", "Fixed deliveries and rolling contract histories.")
        term_product = product_choice(term_key)
        term_key += "_" + term_product
        term_mode = choice("Series", ["Fixed", "Rolling"], term_key + "_mode", control="segmented_control")
        term_bounds = assessment_window(*dates(term_product), term_key + term_mode)

    if archive:
        start = min(*price_bounds, *term_bounds, *curve_targets, *changes_targets,
                    changes_latest - timedelta(days=lookback * 2 + 20))
        warmup = start - timedelta(days=35)
        end = max(*price_bounds, *term_bounds, *curve_targets, *changes_targets)
        years = tuple(year for year in archive["years"] if warmup.year <= year <= end.year)
        if years:
            try:
                history = read_history(repo, credential_id, revision, years, warmup, end, token)
                fixed, rolling = merge_history(fixed, rolling, history)
            except Exception:
                st.warning("Historical assessments are temporarily unavailable. Showing the latest feed.")

    def rows(product, dataset=fixed):
        return dataset[dataset.vessel.eq(vessel) & dataset["product"].eq(product)]

    def price_inputs(product):
        data = rows(product)
        return options(data, rows(product, rolling)), pd.DatetimeIndex(sorted(data.quote_day.unique()))

    basis = {}
    with price_panel:
        local, days = price_inputs(price_product)
        bounds = tuple(pd.Timestamp(day) for day in price_bounds)
        if price_bounds[0] > price_bounds[1]:
            st.info("Choose a start date before the end date.")
        elif price_mode == "Spot + BFA":
            basis = spot_comparison(local, days, bounds, price_key)
        else:
            study_basis, study_calendar = comparison_studies(local, days)
            comparison_workspace(options(fixed, rolling), local, study_basis, study_calendar, bounds,
                                 price_key + price_mode, price_mode == "Spread / Ratio")

    with curve_panel:
        curves(rows(curve_product), fixed, curve_tenor, [pd.Timestamp(day) for day in curve_targets], curve_key)

    with changes_panel:
        curve_changes(rows(changes_product), changes_tenor, change_mode,
                      [pd.Timestamp(day) for day in changes_targets], lookback, changes_key)

    with term_panel:
        local, days = price_inputs(term_product)
        bounds = tuple(pd.Timestamp(day) for day in term_bounds)
        if term_bounds[0] > term_bounds[1]:
            st.info("Choose a start date before the end date.")
        else:
            if term_mode == "Rolling":
                st.caption("Dotted links mark contract rolls or gaps longer than seven days.")
            prices(local, days, bounds, term_key, term_mode)
            calendar_studies = calendar_spreads(
                {leg: value for leg, value in local.items() if leg[6] == term_mode}, days, bounds, term_key + term_mode)
            historical_context(local, days, basis if term_product == price_product else {}, calendar_studies, bounds, term_key)


if __name__ == "__main__":
    main()
