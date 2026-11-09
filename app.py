import hashlib
from html import escape

import pandas as pd
import streamlit as st

from ffa.analytics import frame, options
from ffa.demo import data as demo_data
from ffa.model import VESSELS
from ffa.store import connect, market
from ffa.ui import basis_and_slope, curve_moves, curves, prices, spreads

st.set_page_config(page_title="Baltic FFA | Market Monitor", layout="wide", initial_sidebar_state="auto")

BENCHMARKS = {
    "CAPE": ("C5TC",),
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


def main():
    st.markdown(STYLE, unsafe_allow_html=True)
    try:
        live = st.secrets.get("mode") == "live"
    except FileNotFoundError:
        live = False

    with st.sidebar:
        st.markdown("### Baltic FFA")
        st.caption("DRY FREIGHT RESEARCH")
        vessel = st.radio("Vessel class", list(VESSELS), format_func=VESSELS.get)

    if live:
        try:
            token = st.secrets["github_reader_token"]
            fixed, rolling, _ = read_live(st.secrets["github_repo"], hashlib.sha256(token.encode()).hexdigest(), token)
        except Exception:
            st.error("Market data is temporarily unavailable.")
            return
    else:
        fixed, rolling, _ = demo_data()

    fixed, rolling = frame(fixed), frame(rolling)
    market_data = fixed[fixed.vessel.eq(vessel)]
    if market_data.empty:
        st.title(VESSELS[vessel])
        st.info("No assessments are available for this vessel class.")
        return

    quote_day = market_data.quote_day.max()
    source = "Baltic Exchange assessments" if live else "Synthetic demonstration data"
    st.markdown(
        f'<div class="ffa-hero"><div class="ffa-kicker">Market monitor / Dry bulk</div>'
        f'<h1 class="ffa-title">{VESSELS[vessel]}</h1>'
        f'<div class="ffa-meta">{source} &nbsp;·&nbsp; Latest class quote {quote_day:%d %b %Y}</div></div>',
        unsafe_allow_html=True,
    )
    products = sorted(market_data["product"].unique())
    product_column, context = st.columns([1, 2], vertical_alignment="bottom")
    product = product_column.selectbox("Benchmark or route", products,
                                       index=products.index(default_product(market_data, vessel)),
                                       key=f"product_{vessel}")
    data = market_data[market_data["product"].eq(product)]
    days = pd.DatetimeIndex(sorted(data.quote_day.unique()))
    context.caption(f"{escape(product)}  ·  {days[-1]:%d %b %Y}  ·  {len(days):,} assessment dates")

    market_rolling = rolling[rolling.vessel.eq(vessel) & rolling["product"].eq(product)]
    local = options(data, market_rolling)
    key = f"{vessel}_{product}"
    curve = curves(data, key)
    prices(local, days, key)
    curve_moves(curve, key)
    spreads(local, fixed, rolling, key)
    basis_and_slope(local, days, key)


if __name__ == "__main__":
    main()
