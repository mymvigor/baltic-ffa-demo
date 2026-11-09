import csv
import gzip
import io
import json

import pandas as pd
import streamlit as st

from .analytics import frame
from .store import connect

ARCHIVE_PATH = "history"
IDENTITY = ("vessel", "product", "kind", "currency", "unit", "tenor")
ROLL_KEY = [*IDENTITY, "rolling", "quote_day"]


@st.cache_data(ttl=120, max_entries=4, show_spinner=False)
def read_manifest(repo, credential_id, _token):
    with connect(repo, _token) as storage:
        revision = storage.data_revision()
        raw = storage.read_file(f"{ARCHIVE_PATH}/manifest.json", revision)
    if raw is None:
        return None, {}
    manifest = json.loads(raw)
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported archive format")
    return revision, manifest


@st.cache_data(ttl=300, max_entries=12, show_spinner=False)
def read_history(repo, credential_id, revision, years, start, end, _token):
    first, last = start.isoformat(), end.isoformat()
    with connect(repo, _token) as storage:
        rows = []
        for year in years:
            raw = storage.read_file(f"{ARCHIVE_PATH}/{year}.csv.gz", revision)
            if raw is None:
                raise RuntimeError("Historical data file is missing")
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
                for row in csv.DictReader(io.TextIOWrapper(compressed, encoding="utf-8")):
                    if not first <= row["quote_day"] <= last:
                        continue
                    parts = [row[key] for key in IDENTITY]
                    if row["kind"] == "BFA":
                        parts.extend([row["delivery_start"], row["delivery_end"]])
                    row["identity"] = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
                    row["series"] = None
                    row["source"] = "history"
                    row["mapping"] = "Baltic historical export"
                    rows.append(row)
    return frame(rows)


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
