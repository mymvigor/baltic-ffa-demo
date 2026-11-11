import hashlib
import json
import re

import streamlit as st
from streamlit.components.v2 import component


_browser_memory = component(
    "ffa_browser_preferences",
    html="<span></span>",
    js="""
    export default function({ data, setStateValue }) {
        const key = "baltic-ffa-preferences-v1";
        if (data === null) {
            let saved = {};
            try { saved = JSON.parse(localStorage.getItem(key) || "{}"); } catch {}
            setStateValue("saved", saved);
        } else {
            try { localStorage.setItem(key, JSON.stringify(data)); } catch {}
        }
    }
    """,
)


def restore_preferences():
    memory = st.session_state.setdefault("_ffa_preferences", {})
    loaded = st.session_state.get("_ffa_browser_loaded", False)
    result = _browser_memory(
        data=memory if loaded else None, key="ffa_browser_memory",
        default={"saved": None}, on_saved_change=lambda: None, height=0,
    )
    if loaded:
        return
    saved = result.saved
    if saved is None:
        st.stop()
    if not isinstance(saved, dict):
        saved = {}
    try:
        legacy = json.loads(st.query_params.get("view", "{}"))
        if isinstance(legacy, dict):
            memory.update(legacy)
    except ValueError:
        pass
    memory.update({key: value for key, value in saved.items()
                   if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{12}", key)})
    st.session_state["_ffa_browser_loaded"] = True
    st.rerun()


def _remember(key, default, decode, render):
    memory = st.session_state.setdefault("_ffa_preferences", {})
    short = hashlib.blake2s(key.encode(), digest_size=6).hexdigest()
    widget_key = "ffa_" + short

    def save():
        value = st.session_state[widget_key]
        memory.pop(short, None)
        memory[short] = encode(value)

    encoded = memory.get(short)
    restored = decode(encoded, default)
    current = st.session_state.get(widget_key, restored)
    st.session_state[widget_key] = decode(encode(current), restored)
    return render(restored, widget_key, save)


def encode(value):
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def choice(label, options, key, default=None, *, container=st, control="selectbox", **kwargs):
    options = list(options)
    default = default if default in options else options[0]

    def decode(raw, fallback):
        return next((option for option in options if encode(option) == raw), fallback)

    def render(value, widget_key, save):
        if control == "segmented_control":
            return container.segmented_control(label, options, key=widget_key,
                                               on_change=save, **kwargs) or value
        return getattr(container, control)(label, options, key=widget_key,
                                           on_change=save, **kwargs)
    return _remember(key, default, decode, render)


def multi(label, options, key, default, limit, *, container=st, format_func=str):
    options = list(options)
    lookup = {json.dumps(encode(option)): option for option in options}

    def decode(raw, fallback):
        if not isinstance(raw, list):
            return fallback
        return [lookup[item] for value in raw if (item := json.dumps(value)) in lookup][:limit]

    return _remember(
        key, list(default), decode,
        lambda value, widget_key, save: container.multiselect(
            label, options, key=widget_key, on_change=save,
            max_selections=limit, format_func=format_func,
        ),
    )


def toggle(label, key, default=False, *, container=st):
    return _remember(
        key, default, lambda raw, fallback: raw if isinstance(raw, bool) else fallback,
        lambda value, widget_key, save: container.toggle(label, key=widget_key, on_change=save),
    )


def calendar(label, key, default, earliest, latest, *, container=st):
    from datetime import date

    def decode(raw, fallback):
        try:
            value = date.fromisoformat(raw)
        except (ValueError, TypeError):
            return fallback
        return value if earliest <= value <= latest else fallback

    return _remember(
        key, default, decode,
        lambda value, widget_key, save: container.date_input(
            label, min_value=earliest, max_value=latest, key=widget_key, on_change=save,
        ),
    )
