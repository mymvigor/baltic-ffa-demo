"""Shared market range, signed changes and compact quote tables."""
import pandas as pd
import numpy as np
import streamlit as st
from .preferences import choice, calendar, multi
from .selection import contract_label

UP, DOWN, FLAT = '#18836f', '#bd4656', '#778491'


def window(first, last, key):
    first, last = pd.Timestamp(first), pd.Timestamp(last)
    preset = choice('Time range', ['1M','3M','6M','1Y','All','Custom'], key+'_range', '3M', control='segmented_control')
    if preset == 'Custom':
        a,b=st.columns(2)
        start=calendar('From',key+'_from',max(first,last-pd.DateOffset(months=3)).date(),first.date(),last.date(),container=a)
        end=calendar('To',key+'_to',last.date(),first.date(),last.date(),container=b)
        return pd.Timestamp(start),pd.Timestamp(end)
    months={'1M':1,'3M':3,'6M':6,'1Y':12}
    return (max(first,last-pd.DateOffset(months=months[preset])) if preset in months else first),last


def changes(data):
    """Previous valid quote of the same contract; never a rolling switch."""
    out=data.dropna(subset=['price']).copy()
    valid=out.segment.eq(out.segment.shift()) if 'segment' in out else pd.Series(True,index=out.index)
    previous=out.price.shift()
    out['change']=out.price.diff().where(valid)
    out['change_pct']=(100*out.change/previous.where(previous.abs().gt(1e-12)))
    return out


def bars(fig, data, name='Daily Change', column='change'):
    fig.add_bar(x=data.index,y=data[column],name=name,
                marker_color=[UP if v>0 else DOWN if v<0 else FLAT for v in data[column].fillna(0)],
                customdata=data[['Observed']] if 'Observed' in data else None,
                hovertemplate=('%{customdata[0]}<br>' if 'Observed' in data else '%{x|%d %b %Y}<br>')+'%{y:+,.2f}<extra>%{fullData.name}</extra>')


def signed_style(value):
    return f'color: {UP if value>0 else DOWN if value<0 else FLAT}' if pd.notna(value) else ''


def recent_spot(data, key):
    st.markdown('**Recent Quotes**')
    n=choice('Quote range',[5,10,20],key+'_recent',5,control='segmented_control',format_func=lambda n:f'{n}D')
    quotes=changes(data).tail(n).rename(columns={'price':'Spot','change':'Change','change_pct':'Change %'})
    quotes.index.name='Date'
    table=quotes[['Spot','Change','Change %']].sort_index(ascending=False)
    st.dataframe(table.style.format({'Spot':'{:,.2f}','Change':'{:+,.2f}','Change %':'{:+.2f}%'},na_rep='—').format_index(lambda d:d.strftime('%d %b %Y'))
                 .map(signed_style,subset=['Change','Change %']),width='stretch')


def bfa_table(fixed, asof, n=5, identities=None):
    rows=fixed[fixed.kind.eq('BFA') & fixed.quote_day.le(pd.Timestamp(asof))].copy()
    if identities is not None:rows=rows[rows.identity.isin(identities)]
    dates=sorted(rows.quote_day.unique())[-n:]
    values=[];colors=[]
    for identity,part in rows.groupby('identity',sort=False):
        part=part.sort_values('quote_day').drop_duplicates('quote_day',keep='last')
        if part.empty:continue
        s=part.set_index('quote_day').price.dropna();r=part.iloc[-1]
        previous=s.shift();delta=s.diff();pct=100*delta/previous.where(previous.abs().gt(1e-12))
        row={'Contract':f'{r.vessel} {r["product"]} · {contract_label(r)}','Delivery':r.delivery_start}
        color={}
        for d in dates:
            label=pd.Timestamp(d).strftime('%d %b %Y');row[label]=s.get(d,np.nan);color[label]=signed_style(delta.get(d,np.nan))
        row.update({'Change':delta.iloc[-1] if len(s)>1 else np.nan,'1D %':pct.iloc[-1] if len(s)>1 else np.nan,
                    '5D %':100*(s.iloc[-1]/s.iloc[-6]-1) if len(s)>=6 and abs(s.iloc[-6])>1e-12 else np.nan,
                    'Quote Date':s.index[-1] if len(s) else pd.NaT})
        for col in ['Change','1D %','5D %']:color[col]=signed_style(row[col])
        values.append(row);colors.append(color)
    if not values:return pd.DataFrame(),pd.DataFrame()
    data=pd.DataFrame(values);style=pd.DataFrame(colors).reindex(columns=data.columns).fillna('')
    order=data.sort_values(['Delivery','Contract']).index
    return data.loc[order].drop(columns='Delivery').reset_index(drop=True),style.loc[order].drop(columns='Delivery').reset_index(drop=True)


def recent_bfa(fixed, asof, key):
    st.markdown('**Recent BFA Quotes**')
    n=choice('Quote range',[5,10,20],key+'_bfa_recent',5,control='segmented_control',format_func=lambda n:f'{n}D')
    active=fixed[fixed.kind.eq('BFA') & fixed.delivery_end.ge(pd.Timestamp(asof)) & fixed.quote_day.le(pd.Timestamp(asof))]
    catalog=fixed[fixed.kind.eq('BFA')].sort_values('quote_day').drop_duplicates('identity',keep='last').sort_values(['delivery_start','tenor'])
    if catalog.empty:st.info('No fixed BFA quotes are available.');return
    labels={r.identity:f'{contract_label(r)} · {r["product"]} · {r.currency}/{r.unit}' for _,r in catalog.iterrows()}
    defaults=list(catalog[catalog.identity.isin(active.identity)].identity)[:12]
    selected=multi('Contracts',list(labels),key+'_bfa_contracts',defaults,len(labels),format_func=labels.get)
    data,styles=bfa_table(fixed,asof,n,selected)
    if data.empty:st.info('Select contracts with stored quotes.');return
    formats={c:'{:,.0f}' for c in data if c not in ['Contract','Quote Date']}
    formats.update({'Change':'{:+,.0f}','1D %':'{:+.2f}%','5D %':'{:+.2f}%','Quote Date':lambda d:d.strftime('%d %b %Y') if pd.notna(d) else '—'})
    st.dataframe(data.style.format(formats,na_rep='—').apply(lambda _:styles,axis=None),hide_index=True,width='stretch')
    st.caption('Changes use each fixed contract’s previous valid quotes. Blank cells are missing quotes; Quote Date identifies stale marks.')
