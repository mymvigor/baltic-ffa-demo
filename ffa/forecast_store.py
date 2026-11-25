"""Separate private forecast journal. Never writes to the market-data snapshot."""
import base64
from datetime import datetime, timezone
import gzip
import json
import re

BRANCH = "ffa-data"
INDEX = "forecasts/index.json"
CONFIGS = "forecasts/configs.json"


def day_path(day):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)):
        raise ValueError("Invalid forecast journal date")
    return f"forecasts/by-date/{day}.json.gz"


def decode(raw, path):
    if raw is None:
        return {}
    return json.loads(gzip.decompress(raw) if path.endswith(".gz") else raw)


def read_index(store):
    value = decode(store.read_file(INDEX), INDEX)
    return value if value.get("schema", 1) == 1 else {}


def read_issue_dates(store, contract):
    return read_index(store).get("contracts", {}).get(contract, [])


def read_records(store, contract, limit=6, day=None):
    index = read_index(store)
    dates = index.get("contracts", {}).get(contract, index.get("days", []))
    output = {}
    chosen = [day] if day is not None and day in dates else ([] if day is not None else sorted(dates, reverse=True)[:limit])
    for issued_day in chosen:
        values = decode(store.read_file(day_path(issued_day)), day_path(issued_day))
        for identity, record in values.get("records", {}).items():
            if record.get("contract") == contract:
                output.setdefault(identity, record)
    return output


def _load_for_write(store, path):
    response = store.request("GET", f"/contents/{path}", params={"ref": BRANCH},
                             headers={"Accept": "application/vnd.github.object+json"})
    if response.status_code == 404:
        return {}, None
    if response.status_code != 200:
        raise RuntimeError("Forecast journal read failed")
    entry = response.json()
    if entry.get("encoding") != "base64":
        blob = store.request("GET", f"/git/blobs/{entry['sha']}")
        if blob.status_code != 200:
            raise RuntimeError("Forecast journal blob unavailable")
        entry["content"] = blob.json()["content"]
    return decode(base64.b64decode(entry["content"]), path), entry["sha"]


def update(store, path, change):
    """Compare-and-retry updates; a conflict never replaces another writer's data."""
    for _ in range(3):
        before, sha = _load_for_write(store, path)
        value = json.loads(json.dumps(before))
        change(value)
        if value == before:
            return before
        raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        if path.endswith(".gz"):
            raw = gzip.compress(raw, mtime=0)
        if len(raw) > 20 * 1024 * 1024:
            raise RuntimeError("Daily forecast journal exceeds size limit")
        body = dict(message="Save immutable FFA forecast records", branch=BRANCH,
                    content=base64.b64encode(raw).decode())
        if sha is not None:
            body["sha"] = sha
        response = store.request("PUT", f"/contents/{path}", json=body)
        if response.status_code in (409, 422):
            continue
        if response.status_code not in (200, 201):
            raise RuntimeError("Forecast journal save failed")
        return value
    raise RuntimeError("Forecast journal changed concurrently; try again")


def save_records(store, records):
    by_day = {}
    for record in records:
        if record is not None:
            by_day.setdefault(record["issued_on"], []).append(record)
    for day, items in by_day.items():
        def append(value):
            value.setdefault("schema", 1)
            saved = value.setdefault("records", {})
            for item in items:
                saved.setdefault(item["id"], item)
        update(store, day_path(day), append)
        def index(value):
            value.setdefault("schema", 1)
            value["days"] = sorted(set(value.get("days", [])) | {day})
            contracts = value.setdefault("contracts", {})
            for item in items:
                contracts[item["contract"]] = sorted(set(contracts.get(item["contract"], [])) | {day})
        update(store, INDEX, index)


def activate_configuration(store, record):
    """An explicit user save enables this configuration for subsequent daily runs."""
    def change(value):
        value.setdefault("schema", 1)
        value.setdefault("active", {})[record["contract"]] = dict(
            contract=record["contract"], vessel=record["vessel"], product=record["product"],
            config=record["config"], updated_at=datetime.now(timezone.utc).isoformat())
    update(store, CONFIGS, change)


def active_configurations(store):
    return decode(store.read_file(CONFIGS), CONFIGS).get("active", {})
