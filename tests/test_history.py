import gzip
from contextlib import contextmanager
from datetime import date

import pandas as pd

import ffa.history as history
from test_analysis import quotes


def test_archive_year_cache_is_reused_without_changing_date_filters(monkeypatch):
    rows = quotes(pd.to_datetime(["2026-01-05", "2026-02-02"]), [10, 30], kind="Spot")
    compressed = gzip.compress(rows.to_csv(index=False, date_format="%Y-%m-%d").encode())
    calls = []

    class Storage:
        def read_file(self, path, revision):
            calls.append((path, revision))
            return compressed

    @contextmanager
    def connect(repo, token):
        yield Storage()

    monkeypatch.setattr(history, "connect", connect)
    history.read_history_year.clear()
    history.read_history_scope.clear()
    january = history.read_history("test/repo", "synthetic-id", "revision-a", (2026,), date(2026, 1, 1), date(2026, 1, 31), "synthetic")
    assert january.price.tolist() == [10]
    january.loc[:, "price"] = 999
    february = history.read_history("test/repo", "synthetic-id", "revision-a", (2026,), date(2026, 2, 1), date(2026, 2, 28), "synthetic")
    assert february.price.tolist() == [30]
    assert calls == [("history/2026.csv.gz", "revision-a")]
    history.read_history("test/repo", "synthetic-id", "revision-b", (2026,), date(2026, 2, 1), date(2026, 2, 28), "synthetic")
    assert len(calls) == 2
    history.read_history_year.clear()
    history.read_history_scope.clear()


def test_vectorized_archive_identity_matches_canonical_json(monkeypatch):
    import json

    rows = pd.concat([quotes(pd.to_datetime(["2026-01-05", "2026-01-06"]), [10, 20], kind="Spot"),
                      quotes(pd.to_datetime(["2026-01-05", "2026-01-06"]), [30, 40])], ignore_index=True)
    rows["product"] = 'Quoted "benchmark" \\ variant'
    raw = gzip.compress(rows.to_csv(index=False, date_format="%Y-%m-%d").encode())

    class Storage:
        def read_file(self, path, revision):
            return raw

    @contextmanager
    def connect(*args):
        yield Storage()

    monkeypatch.setattr(history, "connect", connect)
    history.read_history_year.clear()
    history.read_history_scope.clear()
    parsed = history.read_history_year("test/identity", "id", "a1", 2026, "synthetic")
    for row in parsed.itertuples():
        parts = [row.vessel, row.product, row.kind, row.currency, row.unit, row.tenor]
        if row.kind == "BFA":
            parts.extend([f"{row.delivery_start:%Y-%m-%d}", f"{row.delivery_end:%Y-%m-%d}"])
        assert row.identity == json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    assert parsed.identity.nunique() == 2
    selected = history.read_history("test/identity", "id", "a1", (2026,), date(2026, 1, 1), date(2026, 1, 31),
                                    "synthetic", vessel="PMX", product=rows["product"].iloc[0])
    assert selected.empty
    history.read_history_year.clear()
    history.read_history_scope.clear()
