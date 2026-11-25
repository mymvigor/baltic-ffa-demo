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
    if loaded:
        return
    result = _browser_memory(
        data=None, key="ffa_browser_memory",
        default={"saved": None}, on_saved_change=lambda: None, height=0,
    )
    saved = result.saved
    # Render defaults while browser storage is loading. The component callback
    # reruns the page when the saved setting arrives.
    if saved is None:
        return
    if not isinstance(saved, dict):
        saved = {}
    try:
        legacy = json.loads(st.query_params.get("view", "{}"))
        if isinstance(legacy, dict):
            memory.update(legacy)
    except ValueError:
        pass
    for key, value in saved.items():
        if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{12}", key):
            memory.setdefault(key, value)
    st.session_state["_ffa_browser_loaded"] = True
    for name in saved:
        prefix = "ffa_" + name
        if name in memory:
            for key in list(st.session_state):
                if key == prefix or key.startswith(prefix + "_"):
                    del st.session_state[key]
    st.rerun()


def persist_preferences(key):
    """Persist changes after a fragment rerun without rerunning the whole app."""
    if not st.session_state.get("_ffa_browser_loaded"):
        return
    _browser_memory(data=st.session_state.get("_ffa_preferences", {}), key="ffa_memory_" + key,
                    default={"saved": None}, on_saved_change=lambda: None, height=0)


def _remember(key, default, decode, render, domain=None):
    memory = st.session_state.setdefault("_ffa_preferences", {})
    short = hashlib.blake2s(key.encode(), digest_size=6).hexdigest()
    widget_key = "ffa_" + short
    if domain is not None:
        # Streamlit resets a multiselect when its options change. Give each
        # option domain its own widget identity, while retaining logical memory.
        widget_key += "_" + hashlib.blake2s(domain.encode(), digest_size=4).hexdigest()

    def save():
        value = st.session_state[widget_key]
        memory.pop(short, None)
        memory[short] = encode(value)

    encoded = memory.get(short)
    restored = decode(encoded, default)
    current = st.session_state.get(widget_key, restored)
    valid = decode(encode(current), restored)
    # Reassigning the widget on every rerun can overwrite a browser event.
    if widget_key not in st.session_state or current != valid:
        st.session_state[widget_key] = valid
    memory.setdefault(short,encode(valid))
    return render(restored, widget_key, save)


def encode(value):
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return encode(value.item())
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
        # Restore a sensible default when every saved choice became unavailable.
        filtered = [lookup[item] for value in raw if (item := json.dumps(value)) in lookup][:limit]
        return fallback if raw and not filtered else filtered

    return _remember(
        key, list(default), decode,
        lambda value, widget_key, save: container.multiselect(
            label, options, key=widget_key, on_change=save,
            max_selections=limit, format_func=format_func,
        ),
        domain=json.dumps([encode(options), limit]),
    )


def toggle(label, key, default=False, *, container=st):
    return _remember(
        key, default, lambda raw, fallback: raw if isinstance(raw, bool) else fallback,
        lambda value, widget_key, save: container.toggle(label, key=widget_key, on_change=save),
    )


def number(label,key,default,minimum,maximum,step):
    return _remember(key,default,
        lambda raw,fallback:float(raw) if isinstance(raw,(int,float)) and minimum<=raw<=maximum else fallback,
        lambda value,widget_key,save:st.number_input(label,float(minimum),float(maximum),step=float(step),key=widget_key,on_change=save))


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
