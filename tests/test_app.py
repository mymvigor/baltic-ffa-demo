import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
import ffa.preferences as preferences

@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(preferences, "restore_preferences", lambda: None)
    result = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=60)
    result.secrets["mode"] = "demo"
    return result.run()

@pytest.mark.parametrize("vessel", ["CAPE", "PMX", "SMX", "HDY"])
def test_existing_sections_and_open_charts(app, vessel):
    app.sidebar.radio[0].set_value(vessel).run()
    assert not app.exception
    assert not app.dataframe
    plots = app.get("plotly_chart")
    assert len(plots) >= 4
    rendered = " ".join(item.value for item in app.markdown)
    positions = [rendered.index(name) for name in ("Forecast</h2>", "Spot Market</h2>", "Forward Market</h2>", "Market Research</h2>")]
    assert positions == sorted(positions)
    assert not any(w.label == "Market view" for w in app.sidebar.radio)
    assert any({"Spot","MA5","MA20"}.issubset({t.get("name") for t in json.loads(p.proto.spec)["data"]}) for p in plots)

def navigate(app, name):
    assert not app.exception

def test_unified_forward_views(app):
    navigate(app, "Forward Market")
    for axis in ("Calendar Overlay","Actual Date"):
        next(w for w in app.get("segmented_control") if w.label == "Time Axis").set_value(axis).run()
        assert not app.exception
    for mode in ("Normalized","Difference","Ratio","Absolute"):
        next(w for w in app.get("segmented_control") if w.label == "Compare Mode").set_value(mode).run()
        assert not app.exception
    next(w for w in app.get("segmented_control") if w.label == "Forward view").set_value("Forward Curve").run()
    assert not app.exception
    next(w for w in app.get("segmented_control") if w.label == "Forward view").set_value("Repricing").run()
    assert not app.exception
    next(w for w in app.get("segmented_control") if w.label == "Analysis").set_value("Heatmap").run()
    assert not app.exception
    assert not app.dataframe

def test_research_navigation_and_timestamp_guard(app):
    navigate(app,"Market Research")
    next(w for w in app.radio if w.label == "Validation").set_value("Verified timestamps").run()
    assert any("availability timestamps" in w.value for w in app.info)
    next(w for w in app.radio if w.label == "Validation").set_value("Date replay").run()
    for name in ("Lead / lag","Forecast backtest","Relationships"):
        next(w for w in app.get("segmented_control") if w.label == "Research view").set_value(name).run()
        assert not app.exception

def test_forecast_uses_sample_guard_on_demo(app):
    navigate(app,"Forecast")
    assert not app.exception
    assert any("historical Spot years" in w.value for w in app.info)

def test_browser_settings_accept_numpy_years():
    import numpy as np
    from ffa.preferences import encode
    raw = {"years": [np.int64(2026), np.int32(2025)], "flag": np.bool_(True)}
    assert json.loads(json.dumps(encode(raw))) == {"years": [2026, 2025], "flag": True}


def test_multiselect_preserves_selection_when_new_options_arrive():
    result = AppTest.from_string('''
import streamlit as st
from ffa.preferences import multi
if st.button("New year"):
    st.session_state["new_year"] = True
options = [2026, 2025, 2024] if st.session_state.get("new_year") else [2025, 2024]
multi("Years", options, "dynamic_years", options[:1], len(options))
''', default_timeout=15).run()
    result.multiselect[0].set_value([2024]).run()
    result.button[0].click().run()
    assert not result.exception
    assert result.multiselect[0].value == [2024]

def test_predictive_backtest_renders_real_metrics_only_after_sample_guard(monkeypatch):
    result = AppTest.from_string('''
import pandas as pd
from test_predictive import history
from ffa.predictive_ui import predictive_view
predictive_view(history(), (pd.Timestamp('2024-01-01'), pd.Timestamp('2024-12-20')),
                'synthetic_predictive', 'Forecast backtest', ('synthetic-pit',))
''', default_timeout=60).run()
    assert not result.exception
    assert next(widget for widget in result.get("segmented_control") if widget.label == "Sample").value == "Historical peers"
    assert any("Adding Spot vs BFA-only" in item.value for item in result.markdown)
    next(widget for widget in result.selectbox if widget.label == "Score").set_value("accuracy").run()
    assert not result.exception
    for horizon in (3, 5):
        next(widget for widget in result.get("segmented_control") if widget.label == "Forecast horizon").set_value(horizon).run()
        assert not result.exception
        assert any("Adding Spot vs BFA-only" in item.value for item in result.markdown)
    assert not result.dataframe


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
