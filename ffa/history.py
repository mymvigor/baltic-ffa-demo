import gzip
import io
import json

import pandas as pd
import streamlit as st

from .analytics import frame
from .store import connect

ARCHIVE_PATH = "history"
ARCHIVE_REVISION = "f1a3e104f83f72e03b813104b98759e06160c9ee"
IDENTITY = ("vessel", "product", "kind", "currency", "unit", "tenor")
ROLL_KEY = [*IDENTITY, "rolling", "quote_day"]


@st.cache_data(ttl=120, max_entries=4, show_spinner=False)
def read_manifest(repo, credential_id, _token):
    with connect(repo, _token) as storage:
        raw = storage.read_file(f"{ARCHIVE_PATH}/manifest.json", ARCHIVE_REVISION)
    if raw is None:
        return None, {}
    manifest = json.loads(raw)
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported archive format")
    return ARCHIVE_REVISION, manifest


@st.cache_data(max_entries=12, show_spinner=False)
def read_history_year(repo, credential_id, revision, year, _token):
    # Archive revisions are immutable. Reuse the parsed year across date windows.
    with connect(repo, _token) as storage:
        raw = storage.read_file(f"{ARCHIVE_PATH}/{year}.csv.gz", revision)
    if raw is None:
        raise RuntimeError("Historical data file is missing")
    rows = pd.read_csv(io.BytesIO(raw), compression="gzip", dtype=str, keep_default_na=False)
    fields = [*IDENTITY, "delivery_start", "delivery_end"]
    codes, unique = pd.factorize(pd.MultiIndex.from_frame(rows[fields]), sort=False)
    identities = []
    for values in unique:
        parts = list(values[:len(IDENTITY)])
        if values[2] == "BFA":
            parts.extend(values[len(IDENTITY):])
        identities.append(json.dumps(parts, ensure_ascii=False, separators=(",", ":")))
    rows["identity"] = pd.Series(identities).take(codes).to_numpy()
    rows["series"], rows["source"], rows["mapping"] = None, "history", "Baltic historical export"
    return frame(rows)


@st.cache_data(max_entries=32, show_spinner=False)
def read_history_scope(repo, credential_id, revision, year, vessel, product, _token):
    data = read_history_year(repo, credential_id, revision, year, _token)
    if vessel is not None:
        data = data[data.vessel.eq(vessel)]
    if product is not None:
        data = data[data["product"].eq(product)]
    return data


def read_history(repo, credential_id, revision, years, start, end, _token, vessel=None, product=None):
    batches = []
    for year in years:
        data = read_history_scope(repo, credential_id, revision, year, vessel, product, _token)
        batches.append(data[data.quote_day.between(pd.Timestamp(start), pd.Timestamp(end))])
    return pd.concat(batches, ignore_index=True) if batches else frame([])


def merge_history(fixed, rolling, history):
    if history.empty:
        return fixed, rolling
    combined = pd.concat([fixed, history], ignore_index=True).drop_duplicates(["identity", "quote_day"])
    archived_rolls = history[history["rolling"].ne("")].copy()
    archived_rolls["rolling"] = archived_rolls["rolling"].str.split("|", regex=False)
    archived_rolls = archived_rolls.explode("rolling")
    archived_rolls["series"] = "history:" + archived_rolls["rolling"]
    live_keys = pd.MultiIndex.from_frame(rolling[ROLL_KEY])
    archived_keys = pd.MultiIndex.from_frame(archived_rolls[ROLL_KEY])
    merged_rolls = pd.concat([rolling, archived_rolls.loc[~archived_keys.isin(live_keys)]], ignore_index=True)
    return combined, merged_rolls
