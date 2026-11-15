"""Small view switch inside Price Explorer; charts always use the full width."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .predictive import (FEATURES, HORIZONS, MIN_CONDITIONAL, MIN_RELATION, MIN_TEST, MIN_TRAIN_DATES,
                         STAGES, build_samples, relationship, score, select_samples, walk_forward)
from .preferences import choice, toggle
from .ui import COLORS, chart, date_axis, display, draw_segments

VIEWS = ("Price history", "Daily changes", "Lead / lag", "Forecast backtest")


@st.cache_data(max_entries=3, show_spinner=False)
def _dataset(stamp, scope, _fixed):
    return build_samples(_fixed)


@st.cache_data(max_entries=8, show_spinner=False)
def _backtest(stamp, scope, contract, horizon, pooled, historical, selected_stage, bounds, strict, _samples):
    return walk_forward(_samples, contract, horizon, pooled=pooled, historical=historical,
                        selected_stage=selected_stage, bounds=bounds, strict=strict)


def predictive_view(fixed, bounds, key, view, stamp):
    st.markdown("**Spot → BFA · 固定合约验证**")
    eligible = fixed[fixed.currency.eq("USD") & fixed.unit.eq("day")]
    contracts = eligible[eligible.kind.eq("BFA") & eligible.delivery_start.notna()
                         & eligible.delivery_end.notna()].copy()
    if contracts.empty or not eligible.kind.eq("Spot").any():
        st.info("需要同一基准下的 Spot 与固定 BFA 合约，且报价单位为 USD/day。")
        return
    tenor = choice("合约类型", list(dict.fromkeys(contracts.tenor)), key + "_tenor", "month",
                   format_func=lambda item: {"month": "月度", "quarter": "季度", "year": "年度"}.get(item, item))
    contracts = contracts[contracts.tenor.eq(tenor)].drop_duplicates("identity").sort_values("delivery_start")
    labels = contracts.set_index("identity").delivery_start.dt.strftime("%b %Y").to_dict()
    current = eligible.quote_day.max()
    upcoming = contracts[contracts.delivery_start.gt(current)]
    preferred = (upcoming if not upcoming.empty else contracts).iloc[0].identity
    contract = choice("固定合约（单独展示）", labels, key + "_contract_" + tenor, preferred, format_func=labels.get)
    mode = choice("数据时点口径", ["严格时点", "探索回放"], key + "_timing", "严格时点", control="radio", horizontal=True)
    strict = mode == "严格时点"
    if strict:
        st.caption("预测时点：报价日 23:59:59 UTC。仅使用当时已收到的当前版本；缺少可用时间的记录不进入严格样本。")
    else:
        st.warning("探索回放：按报价日期假定日末已知，历史公布时间与修订版本未核验。结果不证明真实可交易的预测能力。")
    scope = (eligible.vessel.iloc[0], eligible["product"].iloc[0], "USD", "day", tenor)
    # The count also invalidates an incomplete archive view after a retry succeeds.
    cache_stamp = (stamp, len(eligible))
    data = _dataset(cache_stamp, scope, eligible[eligible.kind.eq("Spot") | eligible.tenor.eq(tenor)])
    if data.empty:
        st.info("历史报价不足，无法构造变化或未来标签。")
        return
    selected_stage = "all"
    if view != "Daily changes":
        present = [item for item in STAGES if item == "all" or item in set(data.stage)]
        selected_stage = choice("距离交割月", present, key + "_distance", "all", format_func=STAGES.get)
    selected = select_samples(data, contract, selected_stage, strict, bounds)
    if strict and not data.pit.any():
        st.info("严格样本不足：历史存档没有逐日报价的可用时间，无法证明预测时点已经获知这些价格。可切换“探索回放”，两类结果不会混合。")
        return
    st.caption("交易日以同一基准的已存报价日历计数；不跳过缺报价、不填值。未来变化始终来自同一固定合约，不能跨换月。")
    if view == "Daily changes":
        _changes(selected, bounds, key)
    elif view == "Lead / lag":
        historical = toggle("用已结束同类合约扩充关系样本", key + "_relationship_peers")
        if historical:
            selected = select_samples(data[data.tenor.eq(tenor) & data.delivery_end.lt(bounds[1])],
                                      selected_stage=selected_stage, strict=strict, bounds=bounds)
            selected = selected.sort_values(["date", "offset", "contract"]).drop_duplicates("date")
            st.caption("扩充结果属于历史同类合约，不是所选合约单独的结果。每天固定取该期限组内交割起始月最早的一个合约，避免重复计算同日 Spot 信号。")
        _lead_lag(selected, key)
    else:
        pooled = toggle("用已到期同类合约扩充训练（按确切交割月距离对齐）", key + "_pooled_train", default=True)
        historical = toggle("扩充样本外验证：历史同类合约逐合约回放", key + "_pooled_test")
        horizon = choice("预测未来交易日", HORIZONS, key + "_horizon", 1, control="segmented_control")
        if historical:
            st.caption("显示历史同类合约的样本外结果，不归属于所选单一合约。每天只验证期限组内交割起始月最早的一个固定合约。")
        with st.spinner("按时间推进回测…"):
            predictions = _backtest(cache_stamp, scope, contract, horizon, pooled, historical, selected_stage,
                                    bounds, strict, data)
        _forecast(predictions, strict, horizon, key)


def _changes(data, bounds, key):
    pairs = data[["date", "spot_d0", "bfa_d0"]].dropna()
    n = len(pairs)
    st.caption(f"同步变化 · {n} 个配对交易日 · 价格变化单位 USD/day，非收益率百分比。")
    if not n:
        st.info("没有连续两个交易日的配对报价。")
        return
    fig = chart("Daily change · USD/day", height=360)
    for column, name, color in (("spot_d0", "Spot Δ", COLORS[0]), ("bfa_d0", "BFA Δ", COLORS[1])):
        fig.add_scatter(x=data.date, y=data[column], mode="lines+markers", name=name,
                        line=dict(color=color), marker_size=3, connectgaps=False,
                        hovertemplate="%{x|%d %b %Y}<br>%{y:,.2f} USD/day<extra>%{fullData.name}</extra>")
    date_axis(fig, bounds)
    display(fig, key + "_changes")
    if n < MIN_RELATION or pairs.spot_d0.std() == 0 or pairs.bfa_d0.std() == 0:
        st.info(f"至少需要 {MIN_RELATION} 个有效配对日且两者有波动，才显示同步统计。")
        return
    covariance = pairs.spot_d0.cov(pairs.bfa_d0)
    slope = covariance / pairs.spot_d0.var()
    a, b, c = st.columns(3)
    a.metric("同步相关系数", f"{pairs.spot_d0.corr(pairs.bfa_d0):.2f}")
    b.metric("BFA / Spot 反应斜率", f"{slope:.2f}")
    c.metric("同方向比例（含平盘）", f"{(np.sign(pairs.spot_d0) == np.sign(pairs.bfa_d0)).mean():.1%}")
    st.caption("斜率为带截距的同期回归：Spot 每变化 1 USD/day 对应的 BFA 平均变化。同期关系不等于领先或预测。")


def _lead_lag(data, key):
    result = relationship(data)
    measure = choice("关联指标", ["corr", "up", "down"], key + "_lead_measure", "corr",
                     format_func=lambda item: {"corr": "相关系数", "up": "BFA 上涨概率｜Spot 上涨",
                                               "down": "BFA 上涨概率｜Spot 下跌"}[item])
    rows, custom, text = [], [], []
    for lag in range(6):
        line, details, labels = [], [], []
        for h in HORIZONS:
            row = result[result.horizon.eq(h) & result.lag.eq(lag)].iloc[0]
            value = row[measure]
            baseline = f"{row.baseline:.1%}" if pd.notna(row.baseline) else "样本不足"
            interval = "—" if measure == "corr" else f"{row[measure + '_low']:.1%} – {row[measure + '_high']:.1%}"
            line.append(value)
            labels.append("不足" if pd.isna(value) else f"{value:.2f}" if measure == "corr" else f"{value:.0%}")
            details.append([int(row.n), int(row.n_up), int(row.n_down), baseline, interval])
        rows.append(line)
        custom.append(details)
        text.append(labels)
    fig = go.Figure(go.Heatmap(z=rows, x=[f"未来 {h} 日" for h in HORIZONS],
                              y=["Spot 当日变化"] + [f"Spot 滞后 {lag} 日变化" for lag in range(1, 6)],
                              colorscale="RdBu" if measure == "corr" else "Blues", reversescale=measure == "corr",
                              zmin=-1 if measure == "corr" else 0, zmax=1, zmid=0 if measure == "corr" else .5,
                              customdata=custom, text=text, texttemplate="%{text}", hoverongaps=False,
                              hovertemplate="%{y} → %{x}<br>值 %{z:.3f}<br>N=%{customdata[0]}"
                                            "<br>Spot 上涨/下跌样本 %{customdata[1]} / %{customdata[2]}"
                                            "<br>BFA 无条件上涨率 %{customdata[3]}<br>概率 95% 区间 %{customdata[4]}<extra></extra>"))
    fig.update_layout(height=370, margin=dict(l=20, r=20, t=15, b=30), yaxis_autorange="reversed")
    display(fig, key + "_lead_lag")
    st.caption(f"相关系数至少 {MIN_RELATION} 对样本；条件概率每个方向至少 {MIN_CONDITIONAL} 对。未来变化 = BFA(t+h) − BFA(t)。"
               "平盘不计上涨。条件概率区间为 Wilson 描述区间；重叠标签、序列依赖及 18 项关联检验使其不能当作显著性或样本外证据。")
    if result[measure].notna().sum() == 0:
        st.info("当前分组样本不足；可扩大日期范围或明确选择历史同类合约样本。不会据此输出预测结论。")
    st.info("这里只检验关联，不证明 Spot 提供超出 BFA 自身走势的预测信息。请看独立的样本外回测。")


def _forecast(predictions, strict, horizon, key):
    independent, metrics, extra = score(predictions)
    a, b, c = st.columns(3)
    a.metric("逐日样本外预测", len(predictions))
    b.metric("不重叠评估样本", len(independent))
    c.metric("评估合约数", independent.contract.nunique() if not independent.empty else 0)
    st.caption(f"训练至少 {MIN_TRAIN_DATES} 个不同报价日。扩充训练仅用预测时点前已到期、同基准/单位/合约类型且交割月距离完全相同的合约，至少 3 个同类合约。"
               "训练标签结束日严格早于预测日；标准化只拟合训练集。固定 Ridge α=10，不用验证集选参数。")
    if metrics.empty:
        st.info(f"需要至少 {MIN_TEST} 个未来 {horizon} 日区间不重叠的样本外案例，当前不足。"
                "可扩大日期范围或选择历史同类合约验证；样本不足不显示准确率、误差或预测优势结论。")
        return
    measure = choice("回测展示", ["mae", "accuracy"], key + "_score_measure", "mae",
                     format_func=lambda item: "平均绝对预测误差（USD/day）" if item == "mae" else "方向准确率")
    shown = metrics if measure == "mae" else metrics[metrics.accuracy.notna()]
    fig = chart("USD/day" if measure == "mae" else "Accuracy", height=330)
    fig.add_bar(x=shown.model, y=shown[measure], marker_color=COLORS[:len(shown)],
                text=[f"{value:,.1f}" if measure == "mae" else f"{value:.1%}" for value in shown[measure]],
                textposition="outside")
    if measure == "accuracy":
        fig.update_yaxes(range=[0, 1], tickformat=".0%")
    display(fig, key + "_backtest_summary")
    lo, hi = extra["mae_interval"]
    dlo, dhi = extra["accuracy_interval"]
    st.markdown(f"Spot 额外信息检验（同一批样本）：**MAE 改善 {extra['mae_gain']:,.1f} USD/day**，"
                f"时间分块 95% 区间 {lo:,.1f} 至 {hi:,.1f}；"
                f"**方向准确率改善 {extra['accuracy_gain']:+.1%}**，区间 {dlo:+.1%} 至 {dhi:+.1%}。")
    st.caption("改善 = 仅 BFA 模型误差 − 加入 Spot 后的误差；方向改善按准确率差计算。"
               f"按时间分块自助法 1,000 次（块长 {extra['block']}，固定种子）。区间包含 0 时不能确认增益。"
               "方向区分涨/平/跌；价格不变基准只比较误差，不发方向信号。每次预测未来累计变化，并非每日变化。")
    if not strict:
        st.warning("探索回放结果：即使误差或准确率改善，也不能确认真实时点可用的额外预测能力；仍需带公布/接收时间及修订版本的历史数据严格复核。")
    elif lo > 0 and dlo > 0:
        st.success("在当前样本外区间，加入 Spot 同时降低误差并提高方向准确率；增益区间均高于 0。该证据限于本次数据和期限，不保证未来表现。")
    else:
        st.info("当前严格样本外结果未同时确认误差与方向准确率的增益，尚不能认定 Spot 有稳定的额外预测能力。")
