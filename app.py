import hashlib

import pandas as pd
import streamlit as st

from ffa.analytics import frame, options
from ffa.demo import data as demo_data
from ffa.model import VESSELS
from ffa.store import connect, market
from ffa.ui import curves, prices, spreads

st.set_page_config(page_title="Baltic FFA 行情", layout="wide", initial_sidebar_state="expanded")



@st.cache_data(ttl=120, show_spinner=False)
def read_live(repo, credential_id, _token):
    with connect(repo, _token) as storage:
        data, _ = storage.load()
        return market(data)


def main():
    try:
        live = st.secrets.get("mode") == "live"
    except FileNotFoundError:
        live = False
    if live:
        try:
            token = st.secrets["github_reader_token"]
            fixed, rolling, _ = read_live(st.secrets["github_repo"], hashlib.sha256(token.encode()).hexdigest(), token)
        except Exception:
            st.error("行情暂不可读，请检查 GitHub 配置。")
            return
    else:
        st.info("公开演示 · 所有行情均为合成数据")
        fixed, rolling, _ = demo_data()
    page = st.sidebar.radio("船型", list(VESSELS), format_func=VESSELS.get)
    fixed, rolling = frame(fixed), frame(rolling)
    st.title(VESSELS[page])
    market_data = fixed[fixed.vessel == page]
    if market_data.empty:
        st.info("\u6682\u65e0\u884c\u60c5\u3002")
        return

    top, _ = st.columns([2, 3])
    latest = market_data.assign(is_bfa=market_data.kind.eq("BFA")).sort_values(
        ["quote_day", "is_bfa", "product"], ascending=[False, False, True])
    latest_product = latest.iloc[0]["product"]
    products = sorted(market_data["product"].unique())
    product = top.selectbox("产品或指数口径", products, index=products.index(latest_product), key=f"product_{page}")
    data = market_data[market_data["product"] == product]
    days = pd.DatetimeIndex(sorted(data.quote_day.unique()))
    st.caption(f"{product}　·　最新有效报价日 {days[-1]:%Y-%m-%d}　·　BFA 为 Baltic 远期评估价")

    local = options(data, rolling[(rolling.vessel == page) & (rolling["product"] == product)])
    tab_price, tab_spread, tab_curve = st.tabs(["价格", "价差与比值", "远期曲线"])
    key = f"{page}_{product}"
    with tab_price:
        prices(local, days, key)
    with tab_spread:
        spreads(local, fixed, rolling, key)
    with tab_curve:
        curves(data, key)


if __name__ == "__main__":
    main()

