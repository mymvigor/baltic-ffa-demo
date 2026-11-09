import pandas as pd
import streamlit as st

from ffa.analytics import frame, options
from ffa.demo import data as demo_data
from ffa.model import VESSELS
from ffa.ui import curves, prices, spreads

st.set_page_config(page_title="Baltic FFA 行情", layout="wide", initial_sidebar_state="expanded")



def main():
    st.info("公开演示 · 所有行情均为合成数据")
    page = st.sidebar.radio("船型", list(VESSELS), format_func=VESSELS.get)
    fixed, rolling, _ = demo_data()
    fixed, rolling = frame(fixed), frame(rolling)
    st.title(VESSELS[page])
    market_data = fixed[fixed.vessel == page]
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

