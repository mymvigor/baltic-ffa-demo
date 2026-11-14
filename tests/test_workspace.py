import pandas as pd

from ffa.analytics import frame
from ffa.workspace import MarketWorkspace
from test_analysis import quotes


def test_views_reuse_data_but_keep_scope_and_date_windows_independent():
    days = pd.bdate_range("2026-01-01", periods=6)
    rows = quotes(days, range(10, 16), kind="Spot")
    calls = []

    def loader(start, end, vessel, product):
        calls.append((start, end, vessel, product))
        return rows.iloc[:-1]

    workspace = MarketWorkspace(rows.iloc[-1:], frame([]), "quote-v1", "archive-v1")
    a = workspace.view("CAPE", "TEST", days[1], days[-1], loader)
    b = workspace.view("CAPE", "TEST", days[1], days[-1], loader)
    assert a is b
    assert len(calls) == 1
    assert a.fixed.price.tolist() == [15, 10, 11, 12, 13, 14]
    assert calls[0][2:] == ("CAPE", "TEST")
    workspace.view("CAPE", "TEST", days[2], days[-1], loader)
    workspace.view("CAPE", "TEST", days[1], days[-1], loader, wide=True)
    assert len(calls) == 3
    assert calls[-1][2:] == (None, None)


def test_transient_history_failure_is_not_cached_as_complete_history():
    days = pd.bdate_range("2026-01-01", periods=5)
    rows = quotes(days, range(5), kind="Spot")
    calls = []

    def loader(*args):
        calls.append(1)
        if len(calls) == 1:
            missing = frame([])
            missing.attrs["history_unavailable"] = True
            return missing
        return rows.iloc[:-1]

    workspace = MarketWorkspace(rows.iloc[-1:], frame([]), "v1", "a1")
    assert len(workspace.view("CAPE", "TEST", days[0], days[-1], loader).fixed) == 1
    assert len(workspace.view("CAPE", "TEST", days[0], days[-1], loader).fixed) == 5
    assert len(calls) == 2


def test_bounded_cache_and_seasonal_series_omit_fixed_contract_inventory():
    days = pd.bdate_range("2026-01-01", periods=6)
    spot = quotes(days, range(6), kind="Spot")
    forward = quotes(days, range(20, 26))
    rows = pd.concat([spot, forward], ignore_index=True)
    workspace = MarketWorkspace(rows, forward, "v1", "a1", max_views=2)
    loader = lambda *args: frame([])
    seasonal = workspace.view("CAPE", "TEST", days[0], days[-1], loader, seasonal=True)
    assert all(key[2] == "Spot" or key[6] == "Rolling" for key in seasonal.local)
    for start in days[:4]:
        workspace.view("CAPE", "TEST", start, days[-1], loader)
    assert len(workspace.views) == 2
