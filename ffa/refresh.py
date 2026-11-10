import time
from threading import Lock

import streamlit as st

from .store import connect


@st.cache_resource
def refresh_gate(repo):
    return Lock(), {}


def start_refresh(repo, token):
    lock, shared = refresh_gate(repo)
    with lock:
        if time.monotonic() < shared.get("next_dispatch", 0):
            return shared["run"]
        with connect(repo, token) as storage:
            response = storage.request(
                "POST", "/actions/workflows/collect.yml/dispatches",
                headers={"X-GitHub-Api-Version": "2026-03-10"},
                json={"ref": "main", "inputs": {"force": "true"}, "return_run_details": True},
            )
            if response.status_code != 200:
                raise RuntimeError(f"GitHub dispatch failed (HTTP {response.status_code})")
            run = int(response.json()["workflow_run_id"])
        shared.update(run=run, next_dispatch=time.monotonic() + 600)
        return run


def refresh_conclusion(repo, token, run):
    with connect(repo, token) as storage:
        response = storage.request("GET", f"/actions/runs/{run}")
        if response.status_code != 200:
            raise RuntimeError(f"GitHub run status unavailable (HTTP {response.status_code})")
        result = response.json()
        return result["conclusion"] if result["status"] == "completed" else None
