from statistics import mean, stdev

import pandas as pd
import pytest

from ffa.analytics import change_volatility, compare, percentiles, period_means, timeline
from ffa.ui import chart, draw_history_view
from test_analysis import quotes


def test_daily_ratios_are_averaged_after_same_day_pairing():
    days = pd.to_datetime(["2026-01-05", "2026-01-06"])
    a = timeline(quotes(days, [10, 30]), days)
    b = timeline(quotes(days, [2, 3]), days)
    paired = compare(a, b, divide=True, align=True, window=5)
    result = period_means(paired, "price", "M", (days[0], days[-1]))
    assert result.price.tolist() == [7.5]
    assert result.samples.tolist() == [2]


def test_period_averages_do_not_join_two_delivery_contracts():
    days = pd.bdate_range("2026-01-05", periods=4)
    rows = pd.concat([quotes(days[:2], [10, 20]),
                      quotes(days[2:], [100, 200], start="2026-11-01", end="2026-11-30")])
    data = timeline(rows, days)
    fig = chart("USD/day")
    draw_history_view(fig, data, "price", "Weekly mean", 0, (days[0], days[-1]), "Weekly")
    assert [value for value in fig.data[0].y if value is not None] == [15, 150]
    assert fig.data[0].y[1] is None
    assert not fig.data[0].connectgaps


def test_overlay_warmup_uses_prior_days_but_never_prior_contracts():
    days = pd.bdate_range("2026-01-01", periods=45)
    rows = pd.concat([quotes(days[:23], range(1, 24)),
                      quotes(days[23:], range(100, 122), start="2026-11-01", end="2026-11-30")])
    data = timeline(rows, days)
    fig = chart("USD/day")
    bounds = (days[23], days[41])
    draw_history_view(fig, data, "price", "Raw", 0, bounds, "Daily", ["MA20", "Bollinger bands"])
    assert not any("MA20" in trace.name or "band" in trace.name for trace in fig.data)
    fig = chart("USD/day")
    draw_history_view(fig, data, "price", "Raw", 0, (days[42], days[-1]), "Daily", ["MA20"])
    ma = next(trace for trace in fig.data if "MA20" in trace.name)
    assert ma.y[0] == mean(range(100, 120))


def test_percentile_and_volatility_definitions_survive_chart_consolidation():
    days = pd.bdate_range("2026-01-01", periods=12)
    data = timeline(quotes(days, [i * i for i in range(12)]), days)
    data.loc[days[7]:, "segment"] = 2
    result = percentiles(data, window=5)
    assert result.loc[days[4], "median"] == 4
    assert result.loc[days[7]:days[10], "median"].isna().all()
    vol = change_volatility(data, window=5)
    assert vol.loc[days[5]] == pytest.approx(stdev([1, 3, 5, 7, 9]))
    assert vol.loc[days[7]:].isna().all()
