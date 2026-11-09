import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .analytics import compare, delivery_label, indicators, options, snapshots, timeline, unit_label
from .model import TENORS, VESSELS

COLORS = ["#176b83", "#bb5b31", "#6453a4", "#347751", "#a23c71", "#4e657c"]
DASHES = ["solid", "dash", "dot", "dashdot"]


def chart(y):
    fig = go.Figure()
    fig.update_layout(
        template="plotly_white", height=435, margin=dict(l=12, r=14, t=18, b=12),
        hovermode="closest", legend=dict(orientation="h", y=-0.22),
        yaxis=dict(title=y, tickformat=",.3~f", gridcolor="#e8eef2", zerolinecolor="#ccd8df"),
        xaxis=dict(gridcolor="#eff3f6"),
    )
    return fig


def display(fig, key):
    st.plotly_chart(fig, width="stretch", key=key, config={"displaylogo": False, "responsive": True})


def date_bounds(days, key, container=st):
    if len(days) == 1:
        return days[0], days[0]
    bounds = container.date_input("报价日期范围", (days[0].date(), days[-1].date()),
                                  min_value=days[0].date(), max_value=days[-1].date(), key=key)
    if len(bounds) != 2:
        st.info("请选择开始和结束日期。")
        return None
    return pd.Timestamp(bounds[0]), pd.Timestamp(bounds[1])


def format_option(key, series):
    vessel, product, kind, currency, unit, tenor, mode, _ = key
    return f"{VESSELS[vessel]} · {product} · {kind} · {series[key][0]} · {unit_label(currency, unit)}"


def draw_segments(fig, data, column, name, index, hover, custom=None, group=None, dash=None):
    shown = False
    for _, part in data.groupby("segment", sort=False):
        if not part[column].notna().any():
            continue
        fig.add_scatter(
            x=part.index, y=part[column], name=name, mode="lines+markers", connectgaps=False,
            line=dict(color=COLORS[index % len(COLORS)], width=2, dash=dash or DASHES[index % len(DASHES)]),
            marker=dict(size=4), legendgroup=group or name, showlegend=not shown,
            customdata=part[custom] if custom else None, hovertemplate=hover + "<extra>%{fullData.name}</extra>",
        )
        shown = True


def mark_rolls(fig, data, fields, caption="换月"):
    observed = data.dropna(subset=["price"])
    if observed.empty:
        return
    changes = observed[fields].ne(observed[fields].shift()).any(axis=1)
    known = ~observed[fields].isin(["交割期未确认", "—"]).any(axis=1)
    changes &= known & known.shift(fill_value=False)
    for x in observed.index[changes]:
        i = observed.index.get_loc(x)
        before, after = observed.iloc[i - 1], observed.iloc[i]
        details = "　".join(f"{before[field]} → {after[field]}" for field in fields if before[field] != after[field])
        fig.add_scatter(x=[x], y=[after.price], mode="markers", marker=dict(size=9, symbol="diamond-open", color="#657d8c"),
                        name=caption, text=[details], hovertemplate="%{x|%Y-%m-%d}<br>%{text}<extra>换月</extra>",
                        showlegend=False)


def export_csv(df, key):
    # Preserve numeric cells and neutralize spreadsheet expressions in textual cells.
    safe = df.copy()
    for col in safe.select_dtypes(include=["object", "string"]):
        safe[col] = safe[col].map(lambda x: "'" + x if isinstance(x, str) and x.lstrip().startswith(("=", "+", "-", "@")) else x)
    st.download_button("导出当前结果 CSV", safe.to_csv(index=False).encode("utf-8-sig"),
                       "ffa-filter.csv", "text/csv", key=key)


def prices(local, days, key):
    if not local:
        st.info("暂无可绘制序列。")
        return
    left, middle, right = st.columns([1, 1.7, 2.6])
    modes = [mode for mode in ["Rolling", "Fixed"] if any(k[6] == mode for k in local)]
    mode = left.radio("合约口径", modes, horizontal=True, key=key + "_mode")
    bounds = date_bounds(days, key + "_dates", middle)
    all_choices = [k for k in local if k[6] == mode]
    all_choices.sort(key=lambda k: (k[2] == "Spot", -local[k][1].quote_day.max().value, local[k][0]))
    preferred = next((k for k in all_choices if k[2] == "BFA" and k[5] == "month"), all_choices[0])
    unit_choices = list(dict.fromkeys((k[3], k[4]) for k in [preferred, *all_choices]))
    unit = right.selectbox("币种 / 单位", unit_choices, format_func=lambda pair: unit_label(*pair), key=key + mode + "_unit")
    choices = [k for k in all_choices if (k[3], k[4]) == unit]
    selected = right.multiselect("合约", choices, default=[preferred if preferred in choices else choices[0]],
                                 format_func=lambda k: local[k][0], key=key + mode + str(unit) + "_legs")
    spot = next((k for k in local if k[2] == "Spot" and (k[3], k[4]) == unit), None)
    if spot and spot not in selected and st.checkbox("叠加同基准 Spot", key=key + "_spot"):
        selected = [*selected, spot]
    with st.expander("更多选项"):
        overlays = st.multiselect("技术指标（按换月段、有效报价日）", ["5 日均线", "20 日均线", "布林带"],
                                  key=key + "_overlays")
    if not bounds or not selected:
        return
    fig = chart(unit_label(*unit))
    exports = []
    for n, leg in enumerate(selected):
        name, source = local[leg]
        data = indicators(timeline(source, days)).loc[bounds[0]:bounds[1]].copy()
        data["交割期"] = [delivery_label(t, d) if pd.notna(t) else "—" for t, d in zip(data.tenor, data.delivery_start)]
        data["Rolling"] = data["rolling"].fillna("—")
        data["单位"] = unit_label(*unit)
        data["序列"] = name
        hover = "%{x|%Y-%m-%d}<br>%{y:,.3~f} %{customdata[2]}<br>交割 %{customdata[0]}<br>Rolling %{customdata[1]}"
        draw_segments(fig, data, "price", name, n, hover, ["交割期", "Rolling", "单位"])
        if leg[6] == "Rolling":
            mark_rolls(fig, data, ["交割期"])
        for option, columns in {"5 日均线": ["ma5"], "20 日均线": ["ma20"], "布林带": ["upper", "lower"]}.items():
            if option not in overlays:
                continue
            if not data[columns[0]].notna().any():
                st.caption(f"{name} 的{option}样本不足，须在同一换月段积累完整窗口。")
            for col, suffix in zip(columns, [option + ("上轨" if option == "布林带" else ""), "布林带下轨"]):
                draw_segments(fig, data, col, f"{name} · {suffix}", n, hover,
                              ["交割期", "Rolling", "单位"], group=f"{name} · {suffix}", dash="dot")
        exports.append(data.reset_index(names="报价日")[["报价日", "序列", "price", "单位", "交割期", "Rolling", "change", "mapping", "source"]])
    if not fig.data:
        st.info("所选范围没有有效报价。")
    display(fig, key + "_price_chart")
    export_csv(pd.concat(exports), key + "_price_export")


def spreads(local, fixed, rolling, key):
    if not local:
        st.info("暂无可比较序列。")
        return
    all_series = options(fixed, rolling)
    cols = st.columns([2, 2, 1])
    local_keys = sorted(local, key=lambda k: (k[2] == "Spot", k[5] != "month", k[6] != "Rolling", -local[k][1].quote_day.max().value))
    a_key = cols[0].selectbox("A 腿", local_keys, format_func=lambda k: format_option(k, all_series), key=key + "_a")
    compatible = [k for k in all_series if k[3:5] == a_key[3:5] and k != a_key]
    if not compatible:
        st.info("暂无币种和单位可比的第二腿。")
        return
    compatible.sort(key=lambda k: (k[:2] != a_key[:2], k[6] != a_key[6], k[5] != a_key[5], -all_series[k][1].quote_day.max().value))
    b_key = cols[1].selectbox("B 腿", compatible, format_func=lambda k: format_option(k, all_series), key=key + "_b")
    direction = cols[2].radio("计算方向", ["A − B", "A ÷ B"], key=key + "_direction")
    cross = a_key[0] != b_key[0] and a_key[2] == b_key[2] == "BFA"
    align = st.checkbox("跨船型按相同交割区间和粒度对齐", value=True, key=key + "_align") if cross else False
    a_rows, b_rows = all_series[a_key][1], all_series[b_key][1]
    scope = fixed[(fixed.vessel.eq(a_key[0]) & fixed["product"].eq(a_key[1])) |
                  (fixed.vessel.eq(b_key[0]) & fixed["product"].eq(b_key[1]))]
    days = pd.DatetimeIndex(sorted(set(scope.quote_day) | set(a_rows.quote_day) | set(b_rows.quote_day)))
    bounds = date_bounds(days, key + "_spread_dates")
    if not bounds:
        return
    with st.expander("更多选项"):
        show_stats = st.checkbox("显示历史均值、10% / 90% 分位数和 Z-score", key=key + "_stats")
        window = st.number_input("滚动窗口（有效配对数）", 5, 252, 20, key=key + "_window")
    unit = "倍" if direction == "A ÷ B" else unit_label(*a_key[3:5])
    result = compare(timeline(a_rows, days), timeline(b_rows, days), direction == "A ÷ B", align, window)
    data = result.loc[bounds[0]:bounds[1]]
    st.caption(f"{direction} · {unit} · 仅配对同一报价日")
    fig = chart(unit)
    hover = "%{x|%Y-%m-%d}<br>%{y:,.3~f}<br>A %{customdata[0]:,.3~f} · %{customdata[2]}<br>B %{customdata[1]:,.3~f} · %{customdata[3]}"
    draw_segments(fig, data, "price", direction, 0, hover, ["a", "b", "a_period", "b_period"])
    mark_rolls(fig, data, ["a_period", "b_period"])
    if show_stats:
        for n, (field, name) in enumerate([("mean", "历史均值"), ("p10", "10% 分位"), ("p90", "90% 分位")], 1):
            draw_segments(fig, data, field, name, n, "%{x|%Y-%m-%d}<br>%{y:,.3~f}")
        st.caption(f"{window} 个有效配对 · 任一腿换月后重算 · 样本不足时统计为空，零标准差时 Z-score 为空")
        stats = data[["samples", "mean", "p10", "p90", "z"]].rename(
            columns={"samples": "有效样本数", "mean": "历史均值", "p10": "10% 分位", "p90": "90% 分位", "z": "Z-score"})
        st.dataframe(stats.sort_index(ascending=False), width="stretch")
    if not data.price.notna().any():
        st.info("所选范围没有有效配对，请检查日期、交割期及分母。")
    display(fig, key + "_spread_chart")
    export_csv(data.drop(columns=["segment"]).reset_index(names="报价日"), key + "_spread_export")


def curves(data, key):
    data = data[(data.kind == "BFA") & data.delivery_start.notna()]
    if data.empty:
        st.info("暂无已确认实际交割期的 BFA 快照。")
        return
    left, middle, right = st.columns([1, 1, 3])
    tenors = [t for t in ("month", "quarter", "year") if t in set(data.tenor)]
    tenor = left.radio("交割粒度", tenors, format_func=TENORS.get, horizontal=True, key=key + "_curve_tenor")
    data = data[data.tenor == tenor]
    units = list(data[["currency", "unit"]].drop_duplicates().itertuples(index=False, name=None))
    unit = middle.selectbox("币种 / 单位", units, format_func=lambda pair: unit_label(*pair), key=key + "_curve_unit")
    data = data[data.currency.eq(unit[0]) & data.unit.eq(unit[1])]
    days = sorted(data.quote_day.unique(), reverse=True)
    selected = right.multiselect("快照报价日", days, default=[days[i] for i in (0, 5, 20) if len(days) > i],
                                 format_func=lambda d: d.strftime("%Y-%m-%d"), key=key + tenor + str(unit) + "_snapshots")
    if not selected:
        st.info("请选择至少一个已有快照的报价日。")
        return
    by_day = snapshots(data, selected)
    fig = chart(unit_label(*unit))
    fig.update_xaxes(type="category", title="实际交割期")
    for index, (day, snapshot) in enumerate(by_day.items()):
        fig.add_scatter(x=[delivery_label(tenor, expiry) for expiry in snapshot.index], y=snapshot.price,
                        mode="lines+markers", connectgaps=False, name=day.strftime("%Y-%m-%d"),
                        line=dict(color=COLORS[index % len(COLORS)], dash=DASHES[index % len(DASHES)]),
                        hovertemplate="交割 %{x}<br>%{y:,.3~f}<extra>%{fullData.name}</extra>")
    display(fig, key + "_curve_chart")
    if len(selected) >= 2:
        l, r = st.columns(2)
        a = l.selectbox("变动起点", selected, index=1, format_func=lambda d: d.strftime("%Y-%m-%d"), key=key + "_curve_a")
        b = r.selectbox("变动终点", selected, format_func=lambda d: d.strftime("%Y-%m-%d"), key=key + "_curve_b")
        changes = pd.DataFrame({"起点": by_day[a].price, "终点": by_day[b].price})
        changes["变动（终点 − 起点）"] = changes["终点"] - changes["起点"]
        changes.index = [delivery_label(tenor, expiry) for expiry in changes.index]
        st.dataframe(changes, width="stretch")
