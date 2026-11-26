"""Same-date basis, independent annual context and in-delivery implied Spot."""
import numpy as np
import pandas as pd
import streamlit as st
from .settlement import sessions,SUPPORTED
from .selection import contract_label
from .analytics import unit_label


def implied_remaining(bfa,spot,month,asof):
    expected=sessions(month)
    if expected is None:return dict(reason='Verified settlement calendar unavailable.')
    known=expected[expected<=pd.Timestamp(asof)]
    remaining=len(expected)-len(known)
    if remaining<=0:return dict(reason='No unpublished settlement sessions remain.')
    values=spot.reindex(known)
    if values.isna().any():return dict(reason='Published Spot assessments are missing; implied remaining mean unavailable.')
    return dict(value=float((len(expected)*bfa-values.sum())/remaining),known=len(known),remaining=remaining,total=len(expected))


def historical_position(fixed,rolling,leg,anchor,percent=False):
    """One real counterpart/year, within 7 calendar days and exact month distance."""
    kind=rolling if leg[6]=='Rolling' else fixed
    f=kind[kind.vessel.eq(leg[0])&kind['product'].eq(leg[1])&kind.currency.eq(leg[3])&kind.unit.eq(leg[4])&kind.kind.eq('BFA')&kind.tenor.eq(anchor.tenor)].copy()
    if leg[6]=='Rolling':f=f[f['rolling'].eq(leg[7])]
    else:f=f[f.delivery_start.dt.month.eq(anchor.delivery_start.month)]
    spot=fixed[fixed.vessel.eq(leg[0])&fixed['product'].eq(leg[1])&fixed.currency.eq(leg[3])&fixed.unit.eq(leg[4])&fixed.kind.eq('Spot')].drop_duplicates('quote_day').set_index('quote_day').price
    asof=pd.Timestamp(anchor.quote_day)
    offset=(anchor.delivery_start.year-asof.year)*12+anchor.delivery_start.month-asof.month
    offsets=(f.delivery_start.dt.year-f.quote_day.dt.year)*12+f.delivery_start.dt.month-f.quote_day.dt.month
    f=f[offsets.eq(offset)&f.quote_day.lt(asof)&f.quote_day.dt.year.lt(asof.year)].copy()
    f['spot']=f.quote_day.map(spot)
    f['basis']=f.price-f.spot
    if percent:f['basis']=100*f.basis/f.spot.where(f.spot.gt(1e-8))
    f=f.dropna(subset=['basis','delivery_start','delivery_end'])
    values=[]
    for year,part in f.groupby(f.quote_day.dt.year):
        target=asof-pd.DateOffset(years=asof.year-int(year))
        part=part.assign(distance=(part.quote_day-target).abs())
        part=part[part.distance.le(pd.Timedelta(days=7))].sort_values(['distance','quote_day','delivery_start'])
        if len(part):values.append(float(part.basis.iloc[0]))
    return values


def basis_summary(fixed,rolling,leg,raw,data,percent):
    valid=data.dropna(subset=['price','Spot'])
    if valid.empty:
        st.caption(f"{raw.iloc[-1]['product']} · no same-day compatible Spot/BFA observations.");return
    day=valid.index[-1];current=float(valid.price.iloc[-1])
    anchor=raw[raw.quote_day.eq(day)].iloc[-1]
    label=contract_label(anchor).split(' · ')[0] if leg[6]=='Fixed' else f"Rolling {leg[7]}"
    history=historical_position(fixed,rolling,leg,anchor,percent)
    context=f"{len(history)} annual counterparts; percentile unavailable (need 5)."
    if len(history)>=5:
        # Midrank definition, excluding the current observation.
        rank=100*(sum(v<current for v in history)+.5*sum(v==current for v in history))/len(history)
        context=f"Same-season/maturity percentile {rank:.0f}% · {len(history)} annual counterparts."
    unit='%' if percent else unit_label(leg[3],leg[4])
    prior=valid.loc[:day].iloc[:-1]
    prior=prior[prior.segment.eq(valid.segment.iloc[-1])]
    change=f" · last matched quote change {current-float(prior.price.iloc[-1]):+,.2f} {'pp' if percent else unit}" if len(prior) else ''
    st.caption(f"{leg[0]} {leg[1]} {label} · {day:%d %b %Y} · Basis {current:+,.2f} {unit}{change}. {context}")
    if anchor.tenor=='month' and anchor.delivery_start<=day<=anchor.delivery_end and anchor['product'] in SUPPORTED and leg[3:5]==('USD','day'):
        spot=fixed[fixed.vessel.eq(leg[0])&fixed['product'].eq(leg[1])&fixed.kind.eq('Spot')&fixed.currency.eq(leg[3])&fixed.unit.eq(leg[4])&fixed.quote_day.le(day)].drop_duplicates('quote_day').set_index('quote_day').price
        value=implied_remaining(float(anchor.price),spot,anchor.delivery_start.to_period('M'),day)
        if 'value' in value:
            st.caption(f"Market-implied remaining Spot mean {value['value']:,.0f} USD/day · {value['known']} published + {value['remaining']} remaining sessions. (Total sessions × BFA − published Spot sum) / remaining sessions; not a forecast.")
            if value['value']<0:st.caption('Negative implied value is an algebraic implication. Check quote consistency and contract rules before interpreting it.')
        else:st.caption(value['reason'])
