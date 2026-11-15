import numpy as np
import pandas as pd
import pytest

from ffa.analytics import frame
from ffa.predictive import (FEATURES, build_samples, independent_forecasts, relationship,
                            score, walk_forward)


def history():
    rng = np.random.default_rng(42)
    days = pd.bdate_range("2023-01-02", "2024-12-20")
    spot_changes = rng.normal(0, 50, len(days))
    spot = 20000 + spot_changes.cumsum()
    rows = []
    for i, day in enumerate(days):
        rows.append(dict(identity="Spot", quote_day=day, vessel="PMX", product="TEST", kind="Spot",
                         currency="USD", unit="day", tenor="spot", delivery_start=None, delivery_end=None,
                         price=spot[i], received_at=(day.tz_localize("UTC") + pd.Timedelta(hours=16)).isoformat()))
    for start in pd.date_range("2023-02-01", "2025-01-01", freq="MS"):
        end = start + pd.offsets.MonthEnd()
        # Synthetic Spot leads the next day's BFA change; target stays fixed.
        bfa = 21000 + np.r_[0, .8 * spot_changes[:-1]].cumsum() + rng.normal(0, 2, len(days))
        for i, day in enumerate(days):
            if start - pd.DateOffset(months=4) <= day <= end:
                rows.append(dict(identity=str(start.date()), quote_day=day, vessel="PMX", product="TEST", kind="BFA",
                                 currency="USD", unit="day", tenor="month", delivery_start=start, delivery_end=end,
                                 price=bfa[i], received_at=(day.tz_localize("UTC") + pd.Timedelta(hours=17)).isoformat()))
    return frame(rows)


@pytest.fixture(scope="module")
def samples():
    return build_samples(history())


def test_calendar_targets_stay_in_one_fixed_contract_and_do_not_skip_missing_quotes():
    rows = history()
    days = pd.DatetimeIndex(sorted(rows.quote_day.unique()))
    contract = "2024-01-01"
    data = build_samples(rows)
    sample = data[(data.contract == contract) & data.date.eq("2023-12-11")].iloc[0]
    raw = rows[rows.identity.eq(contract)].set_index("quote_day")
    assert sample.target_3 == pytest.approx(raw.loc["2023-12-14", "price"] - raw.loc["2023-12-11", "price"])
    assert sample.label_day_3 == pd.Timestamp("2023-12-14")
    assert data[data.contract.eq(contract) & data.date.eq("2024-01-31")].target_1.isna().all()
    removed = rows[~(rows.identity.eq(contract) & rows.quote_day.eq("2023-12-12"))]
    missing = build_samples(removed)
    assert missing[missing.contract.eq(contract) & missing.date.eq("2023-12-11")].target_3.isna().all()
    assert len(days) > 400


def test_pit_gates_missing_late_and_revised_vintages():
    rows = history()
    assert rows.available_at.notna().all()
    strict = build_samples(rows)
    assert strict.pit.any()
    late = rows.copy()
    late["available_at"] += pd.Timedelta(days=1000)
    assert not build_samples(late).pit.any()
    undated = rows.copy()
    undated["available_at"] = pd.NaT
    assert not build_samples(undated).pit.any()


def test_walk_forward_purges_labels_aligns_tenor_and_uses_only_expired_peers(samples):
    result = walk_forward(samples, "2024-01-01", 5, pooled=True, strict=True,
                          bounds=(pd.Timestamp("2023-11-01"), pd.Timestamp("2024-01-30")))
    assert len(result) > 10
    assert (result.train_last_label < result.date).all()
    assert (result.train_latest_available <= result.decision_at).all()
    assert (result.train_peer_latest_end < result.date).all()
    assert (result.train_offset_min == result.offset).all()
    assert (result.train_offset_max == result.offset).all()
    assert (result.train_dates >= 60).all()
    assert result.contract.eq("2024-01-01").all()


def test_future_data_perturbation_cannot_change_earlier_predictions(samples):
    cutoff = pd.Timestamp("2023-11-20")
    bounds = (pd.Timestamp("2023-11-01"), cutoff)
    before = walk_forward(samples, "2024-01-01", 3, pooled=True, strict=True, bounds=bounds)
    modified = samples.copy()
    modified.loc[modified.date.gt(cutoff), FEATURES] += 1e6
    modified.loc[modified.label_day_3.ge(cutoff), "target_3"] += 1e6
    after = walk_forward(modified, "2024-01-01", 3, pooled=True, strict=True, bounds=bounds)
    pd.testing.assert_frame_equal(before[["date", "BFA only", "BFA + Spot"]], after[["date", "BFA only", "BFA + Spot"]])


def test_unknown_timestamps_cannot_silently_enter_strict_backtest(samples):
    unknown = samples.copy()
    unknown["pit"] = False
    assert walk_forward(unknown, "2024-01-01", 1, pooled=True, strict=True).empty
    assert not walk_forward(unknown, "2024-01-01", 1, pooled=True, strict=False).empty


def test_nonoverlap_scoring_and_insufficient_sample_guard():
    dates = pd.bdate_range("2024-01-01", periods=15)
    predictions = pd.DataFrame(dict(date=dates, label_day=dates + pd.offsets.BDay(3), offset=1,
                                    contract="c", actual=1., **{"Unchanged": 0., "BFA momentum": -1., "BFA only": .5, "BFA + Spot": 1.}))
    kept = independent_forecasts(predictions)
    assert len(kept) == 5
    assert (kept.date.iloc[1:].to_numpy() >= kept.label_day.iloc[:-1].to_numpy()).all()
    assert score(predictions)[1].empty
    _, metrics, extra = score(predictions, min_test=5)
    assert metrics.set_index("model").loc["BFA + Spot", "mae"] == 0
    assert extra["mae_gain"] == .5


def test_conditional_up_probability_has_its_own_denominator_and_small_sample_guard():
    data = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=40)})
    for lag in range(6):
        data[f"spot_d{lag}"] = [1] * 25 + [-1] * 15
    for h in [1, 3, 5]:
        data[f"target_{h}"] = [1] * 20 + [-1] * 20
    row = relationship(data).iloc[0]
    assert row.up == .8
    assert row.baseline == .5
    assert row.n_up == 25 and row.n_down == 15
    assert pd.isna(row.down)
    assert relationship(data.iloc[:10])["corr"].isna().all()


def test_mixed_benchmarks_cannot_be_pooled():
    rows = history().iloc[:20].copy()
    rows.loc[rows.index[-1], "product"] = "OTHER"
    with pytest.raises(ValueError, match="one benchmark"):
        build_samples(rows)


def test_known_synthetic_leading_signal_is_detected_out_of_sample(samples):
    predictions = walk_forward(samples, "2024-01-01", 1, pooled=True, strict=True)
    independent, metrics, _ = score(predictions)
    assert len(independent) >= 30
    error = metrics.set_index("model").mae
    assert error["BFA + Spot"] < .5 * error["BFA only"]
