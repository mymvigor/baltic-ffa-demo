"""Shared searchable labels; no mandatory filter-menu hierarchy."""
import pandas as pd
from .preferences import choice


def contract_label(row):
    start=pd.Timestamp(row.delivery_start)
    tenor=row.tenor
    if tenor=="month": name=start.strftime("%b%y").upper();category="Monthly"
    elif tenor=="quarter": name=f"Q{start.quarter}{start:%y}";category="Quarterly"
    else: name=f"CAL{start:%y}";category="Yearly"
    return f"{name} · Fixed · {start.year} · {category}"


def contract_choice(label,rows,key,default=None):
    rows=rows.sort_values("quote_day").drop_duplicates("identity",keep="last")
    labels={r.identity:contract_label(r) for r in rows.itertuples()}
    today=rows.quote_day.max()
    indexed=rows.set_index("identity")
    ordered=sorted(labels,key=lambda identity:(indexed.loc[identity].delivery_end<today,abs((indexed.loc[identity].delivery_start-today).days),labels[identity]))
    return choice(label,ordered,key,default,format_func=labels.get)
