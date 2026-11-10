import hashlib
import json

import streamlit as st


def _remember(key, default, decode, render, persist):
    memory = st.session_state.setdefault("_ffa_preferences", {})
    if persist and "_ffa_url_loaded" not in st.session_state:
        try:
            stored = json.loads(st.query_params.get("view", "{}"))
            if isinstance(stored, dict):
                memory.update(stored)
        except ValueError:
            pass
        st.session_state["_ffa_url_loaded"] = True

    short = hashlib.blake2s(key.encode(), digest_size=6).hexdigest()
    widget_key = "ffa_" + short

    def save():
        value = st.session_state[widget_key]
        memory.pop(short, None)
        memory[short] = encode(value)
        if persist:
            stored = {k: v for k, v in memory.items() if not k.startswith("_")}
            while len(json.dumps(stored, separators=(",", ":"))) > 4200 and len(stored) > 1:
                del stored[next(iter(stored))]
            st.query_params["view"] = json.dumps(stored, separators=(",", ":"))

    encoded = memory.get(short)
    restored = decode(encoded, default)
    if widget_key in st.session_state:
        current = st.session_state[widget_key]
        if decode(encode(current), default) != current:
            st.session_state.pop(widget_key)
    return render(restored, widget_key, save)


def encode(value):
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def choice(label, options, key, default=None, *, container=st, control="selectbox", persist=True, **kwargs):
    options = list(options)
    default = default if default in options else options[0]

    def decode(raw, fallback):
        return next((option for option in options if encode(option) == raw), fallback)

    def render(value, widget_key, save):
        if control == "segmented_control":
            return container.segmented_control(label, options, default=value, key=widget_key,
                                               on_change=save, **kwargs) or value
        return getattr(container, control)(label, options, index=options.index(value), key=widget_key,
                                           on_change=save, **kwargs)
    return _remember(key, default, decode, render, persist)


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
            label, options, default=value, key=widget_key, on_change=save,
            max_selections=limit, format_func=format_func,
        ), True,
    )


def toggle(label, key, default=False, *, container=st):
    return _remember(
        key, default, lambda raw, fallback: raw if isinstance(raw, bool) else fallback,
        lambda value, widget_key, save: container.toggle(label, value=value, key=widget_key, on_change=save), True,
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
            label, value=value, min_value=earliest, max_value=latest, key=widget_key, on_change=save,
        ), True,
    )
