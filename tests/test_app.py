import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import ffa.preferences as preferences


@pytest.fixture
def app(monkeypatch):
    # AppTest has no browser JS; preference restoration is verified in the browser.
    monkeypatch.setattr(preferences, "restore_preferences", lambda: None)
    result = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=30)
    result.secrets["mode"] = "demo"
    return result.run()


@pytest.mark.parametrize("vessel", ["CAPE", "PMX", "SMX", "HDY"])
def test_existing_sections_and_open_charts(app, vessel):
    app.sidebar.radio[0].set_value(vessel).run()
    assert not app.exception
    assert not app.expander
    rendered = " ".join(item.value for item in app.markdown)
    for name in ("Spot Analysis", "Price Explorer", "Term Structure", "Market Relationships", "Seasonality"):
        assert name in rendered
    assert 8 <= len(app.get("plotly_chart")) <= 10
    assert not app.dataframe
    merged_ma = [item for item in app.get("plotly_chart") if {"Spot", "MA5", "MA20"}.issubset(
        {trace.get("name") for trace in json.loads(item.proto.spec)["data"]})]
    assert len(merged_ma) == 1
    assert any(widget.label == "Spot monthly mean" and widget.value for widget in app.toggle)


def test_modes_preserve_existing_research_views(app):
    analyses = [widget for widget in app.get("segmented_control") if widget.label == "Analysis"]
    analyses[0].set_value("Multi-series").run()
    assert not app.exception
    analyses = [widget for widget in app.get("segmented_control") if widget.label == "Analysis"]
    analyses[0].set_value("Spread / Ratio").run()
    assert not app.exception
    analyses = [widget for widget in app.get("segmented_control") if widget.label == "Analysis"]
    analyses[1].set_value("Heatmap").run()
    assert not app.exception
    analyses = [widget for widget in app.get("segmented_control") if widget.label == "Analysis"]
    analyses[0].set_value("Spot + BFA").run()
    next(widget for widget in app.get("segmented_control") if widget.label == "Contract history").set_value("Fixed").run()
    assert not app.exception
    assert any("unadjusted" in item.value for item in app.caption)
    assert any("heatmap" == trace["type"] for item in app.get("plotly_chart")
               for trace in json.loads(item.proto.spec)["data"])


def test_spot_monthly_button_and_seasonal_rolling(app):
    next(widget for widget in app.toggle if widget.label == "Spot monthly mean").set_value(False).run()
    assert not app.exception
    specs = [json.loads(item.proto.spec) for item in app.get("plotly_chart")]
    assert not any(trace.get("name") == "Spot monthly mean" for spec in specs for trace in spec["data"])
    selector = next(widget for widget in app.selectbox if widget.label == "Seasonal series")
    assert all("Spot" in item or "Rolling" in item for item in selector.options)
    selector.select(next(item for item in selector.options if "Rolling" in item)).run()
    assert not app.exception
    assert next(widget for widget in app.get("segmented_control") if widget.label == "Seasonal frequency").options == ["Daily"]


def test_frequency_and_indicators_reuse_the_existing_chart(app):
    before = len(app.get("plotly_chart"))
    next(widget for widget in app.multiselect if widget.label == "Indicators").set_value(["MA5", "MA20", "Bollinger bands", "20D percentiles"]).run()
    assert not app.exception
    assert len(app.get("plotly_chart")) == before
    names = [trace.get("name", "") for item in app.get("plotly_chart") for trace in json.loads(item.proto.spec)["data"]]
    assert any("10th percentile" in name for name in names)
    next(widget for widget in app.selectbox if widget.label == "Frequency").set_value("Monthly").run()
    assert not app.exception
    assert len(app.get("plotly_chart")) == before
    assert not app.dataframe


def test_all_benchmarks_and_rebased_mode_still_work(app):
    next(widget for widget in app.get("segmented_control") if widget.label == "Analysis").set_value("Multi-series").run()
    next(widget for widget in app.selectbox if widget.label == "Universe").set_value("All benchmarks").run()
    assert not app.exception
    next(widget for widget in app.radio if widget.label == "View").set_value("Rebased to 100").run()
    assert not app.exception
    assert any("Base date" in item.value for item in app.caption)


def test_same_day_revision_invalidates_session_views(monkeypatch):
    monkeypatch.setattr(preferences, "restore_preferences", lambda: None)
    result = AppTest.from_string('''
import streamlit as st
import app as dashboard
from ffa.analytics import frame
from ffa.demo import data
raw, relative, status = data()
version = st.session_state.get("test_revision", 0)
if st.button("Revise same-date prices"):
    version += 1
    st.session_state["test_revision"] = version
fixed, rolling = frame(raw), frame(relative)
fixed.loc[fixed.kind.eq("Spot"), "price"] += version * 100
dashboard.read_live = lambda *args: (fixed, rolling, status, str(version))
dashboard.read_live.clear = lambda: None
dashboard.read_manifest = lambda *args: (None, {})
dashboard.main()
''', default_timeout=30)
    result.secrets.update({"mode": "live", "github_repo": "test/repo", "github_reader_token": "synthetic"})
    result.run()
    assert not result.exception
    before = float(result.metric[0].value.replace(",", ""))
    previous_day = result.session_state["_market_workspace"].fixed.quote_day.max()
    next(widget for widget in result.button if widget.label == "Revise same-date prices").click().run()
    assert not result.exception
    assert float(result.metric[0].value.replace(",", "")) == before + 100
    assert result.session_state["_market_workspace"].fixed.quote_day.max() == previous_day


def test_multi_year_seasonal_prices_and_leap_alignment(monkeypatch):
    result = AppTest.from_string('''
import pandas as pd
from ffa.analytics import frame, options
from ffa.spot import seasonality
rows = []
for year in range(2022, 2027):
    for n, day in enumerate(pd.bdate_range(f"{year}-01-01", periods=30)):
        for kind in ("Spot", "BFA"):
            start = day.to_period("M").to_timestamp() if kind == "BFA" else None
            rows.append(dict(identity=f"{kind}:{start}", quote_day=day, vessel="CAPE", product="TEST",
                             kind=kind, currency="USD", unit="day", tenor="month" if start else "spot",
                             delivery_start=start, delivery_end=start + pd.offsets.MonthEnd() if start else None,
                             rolling="+1MON" if start else None, series="SYNTH", price=year * 10 + n))
fixed = frame(rows)
rolling = fixed[fixed.kind.eq("BFA")]
seasonality(options(fixed, rolling), pd.DatetimeIndex(sorted(fixed.quote_day.unique())), [2022,2023,2024,2025,2026], "test")
''', default_timeout=20).run()
    assert not result.exception
    spec = json.loads(result.get("plotly_chart")[0].proto.spec)
    assert [trace["name"] for trace in spec["data"]] == [str(year) for year in range(2026, 2021, -1)]
    selector = next(widget for widget in result.selectbox if widget.label == "Seasonal series")
    selector.select(next(item for item in selector.options if "Rolling" in item)).run()
    assert not result.exception
    spec = json.loads(result.get("plotly_chart")[0].proto.spec)
    current = next(trace for trace in spec["data"] if trace["name"] == "2026")
    assert None in current["y"]
    assert [value for value in current["y"] if value is not None] == list(range(20260, 20290))
    marker = next(trace for trace in spec["data"] if trace["name"] == "2026 · Roll")
    assert all(item[0].startswith("2026-") for item in marker["customdata"])
