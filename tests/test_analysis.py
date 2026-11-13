from math import sqrt
from statistics import mean, pstdev

import pandas as pd
import pytest

from ffa.analytics import compare, frame, indicators, monthly_spot, options, seasonal, timeline
from ffa.ui import chart, draw_segments, mark_rolls


def quotes(days, values, kind="BFA", start="2026-10-01", end="2026-10-31", series="CURMON"):
    return frame([dict(identity=f"{kind}:{start}:{end}", quote_day=day, vessel="CAPE", product="TEST",
                       kind=kind, currency="USD", unit="day", tenor="spot" if kind == "Spot" else "month",
                       delivery_start=None if kind == "Spot" else start,
                       delivery_end=None if kind == "Spot" else end, price=value,
                       rolling=None if kind == "Spot" else "CURMON", series=series,
                       mapping="synthetic", source="synthetic") for day, value in zip(days, values)])


def test_raw_roll_gap_and_exact_window_restarts_with_missing_quotes():
    days = pd.bdate_range("2026-09-01", periods=47)
    old = quotes(days[:23], range(1, 24))
    new = quotes(days[23:], range(100, 124), start="2026-11-01", end="2026-11-30")
    new.loc[new.quote_day.eq(days[25]), "price"] = float("nan")
    rows = pd.concat([old, new], ignore_index=True)
    result = indicators(timeline(rows, days))
    assert result.loc[days[22], "price"] == 23
    assert result.loc[days[23], "price"] == 100
    assert pd.isna(result.loc[days[23], "change"])
    assert result.loc[days[23]:days[27], "ma5"].isna().all()
    assert result.loc[days[28], "ma5"] == mean([100, 101, 103, 104, 105])
    # The missing row counts neither as a zero nor as a valid trading observation.
    assert result.loc[days[23]:days[42], "ma20"].isna().all()
    values = [100 + i for i in range(21) if i != 2]
    assert result.loc[days[43], "ma20"] == mean(values)
    assert result.loc[days[43], "upper"] == pytest.approx(mean(values) + 2 * pstdev(values))
    assert result.loc[days[43], "lower"] == pytest.approx(mean(values) - 2 * pstdev(values))
    assert pd.isna(result.loc[days[26], "change"])
    assert result.loc[days[25], ["ma5", "ma20", "upper", "lower"]].isna().all()

    fig = chart("USD/day")
    draw_segments(fig, result, "price", "Raw", 0, "%{y}", level=True)
    assert len(fig.data) == 2
    assert fig.data[0].y[22:25] == (23, None, 100)
    assert not fig.data[0].connectgaps
    assert list(fig.data[1].x) == [days[23]]
    assert fig.data[1].customdata[0][0] == days[23].strftime("%Y-%m-%d")


def test_contract_end_changes_and_unresolved_periods_cannot_share_windows():
    days = pd.bdate_range("2026-09-01", periods=25)
    rows = quotes(days, range(25))
    rows.loc[20:, "delivery_end"] = pd.Timestamp("2026-11-30")
    result = indicators(timeline(rows, days))
    assert result.loc[days[20]:, "ma20"].isna().all()
    rows.loc[:, ["delivery_start", "delivery_end"]] = pd.NaT
    result = indicators(timeline(rows, days))
    assert result.ma5.isna().all()
    assert result.change.isna().all()
    assert result.price.tolist() == list(range(25))


def test_spot_continues_through_month_end_and_warms_before_display_bounds():
    days = pd.bdate_range("2026-09-14", periods=30)
    result = indicators(timeline(quotes(days, range(1, 31), kind="Spot"), days))
    assert result.segment.nunique() == 1
    assert pd.isna(result.iloc[18].ma20)
    assert result.loc[days[19]:].iloc[0].ma20 == mean(range(1, 21))
    assert result.iloc[19].upper == pytest.approx(10.5 + 2 * sqrt(33.25))


def test_monthly_means_keep_missing_months_and_partial_coverage():
    days = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-03-02", "2026-03-03"])
    rows = quotes(days, [10, 30, float("nan"), 80, 100], kind="Spot")
    monthly = monthly_spot(rows)
    assert monthly.price.iloc[0] == 20
    assert monthly.samples.tolist() == [2, 0, 2]
    assert pd.isna(monthly.price.iloc[1])
    assert monthly.price.iloc[2] == 90
    assert monthly.index[-1] == pd.Timestamp("2026-03-03")
    assert monthly.first_day.iloc[-1] == pd.Timestamp("2026-03-02")
    assert monthly.last_day.iloc[-1] == pd.Timestamp("2026-03-03")
    assert monthly_spot(rows.iloc[:0]).empty


def test_calendar_alignment_keeps_leap_day_and_raw_prices_and_segments():
    days = pd.to_datetime(["2023-03-01", "2024-02-28", "2024-02-29", "2024-03-01"])
    data = pd.DataFrame({"price": [10, 20, 30, 40], "segment": [1, 2, 2, 3]}, index=days)
    aligned = seasonal(data)
    assert aligned.season_day.dt.strftime("%m-%d").tolist() == ["03-01", "02-28", "02-29", "03-01"]
    pd.testing.assert_series_equal(aligned.price, data.price)
    pd.testing.assert_series_equal(aligned.segment, data.segment)


def test_archive_and_live_rolling_rows_keep_the_same_contract_history():
    days = pd.bdate_range("2026-09-01", periods=10)
    a = quotes(days[:5], range(5), series="history:CURMON")
    b = quotes(days[5:], range(5, 10), series="live-feed")
    rolling = pd.concat([a, b], ignore_index=True)
    choices = options(a.iloc[:0], rolling)
    assert len(choices) == 1
    source = next(iter(choices.values()))[1]
    assert len(source) == 10
    result = indicators(timeline(source, days))
    assert result.iloc[-1].ma5 == 7


def test_spread_statistics_match_independent_window_and_reset_at_either_roll():
    days = pd.bdate_range("2026-09-01", periods=12)
    a = timeline(quotes(days, range(10, 22)), days)
    b = timeline(quotes(days, [2] * 12), days)
    b.loc[days[7]:, "segment"] = 2
    spread = compare(a, b, divide=False, align=True, window=5)
    assert spread.loc[days[4], "mean"] == mean([8, 9, 10, 11, 12])
    assert spread.loc[days[4], "p10"] == pytest.approx(8.4)
    assert spread.loc[days[4], "z"] == pytest.approx((12 - 10) / pstdev([8, 9, 10, 11, 12]))
    assert spread.loc[days[7]:days[10], "mean"].isna().all()
    assert spread.loc[days[11], "mean"] == mean([15, 16, 17, 18, 19])
    assert spread.loc[days[7], "samples"] == 1
    b["price"] = 0
    assert compare(a, b, divide=True, align=True, window=5).price.isna().all()
