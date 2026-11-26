"""One-page position workbench. Only acknowledged GitHub batches affect P&L."""
from copy import deepcopy
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import hashlib
import uuid
import pandas as pd
import streamlit as st
from .positions import empty_book,calculate,risk_state
from .position_store import load,DEFAULT_BRANCH,Conflict
from .position_sync import contract_catalog,sync_batch
from .position_excel import report
from .position_grid import grid
from .store import connect
from .preferences import multi
from .quote_views import UP,DOWN,FLAT,signed_style,bars
from .ui import chart,date_axis,display


def rerender():
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    ctx=get_script_run_ctx()
    st.rerun(scope='fragment' if ctx and ctx.fragment_ids_this_run else 'app')


def config():
    try:
        repo=st.secrets.get('positions_repo',st.secrets.get('github_repo'))
        branch=st.secrets.get('positions_branch',DEFAULT_BRANCH)
        writer=st.secrets.get('positions_writer_token')
        reader=st.secrets.get('positions_reader_token',st.secrets.get('github_reader_token')) or writer
        return repo,branch,reader,writer
    except FileNotFoundError:return None,DEFAULT_BRANCH,None,None


def styled(table):
    fmt={c:'{:+,.2f}' for c in table if 'P&L' in c}
    fmt.update({c:'{:,.2f}' for c in ('Average Cost','Latest BFA','Quantity') if c in table})
    for c in ('Date','Quote Date'):
        if c in table:fmt[c]=lambda d:pd.Timestamp(d).strftime('%d %b %Y') if pd.notna(d) else '—'
    columns=[c for c in table if 'P&L' in c]
    value=table.style.format(fmt,na_rep='—')
    return value.map(signed_style,subset=columns) if columns else value


@st.cache_data(max_entries=16,show_spinner=False)
def valued(account,trades,asof,version,_fixed):
    return calculate(trades,_fixed,asof)


def position_management(loader,latest,refresh,data_version=None,synthetic=False):
    st.title('Position Management')
    repo,branch,reader,writer=config()
    if synthetic:
        repo=reader=writer=None
        st.caption('Synthetic quotes. Private account records are not loaded or written.')
    account=hashlib.sha256(f'{repo or "demo"}:{branch}'.encode()).hexdigest()[:20]
    key='_positions_'+account
    credential=hashlib.sha256((reader or '').encode()).hexdigest()[:12]
    refresh_version=refresh('positions');latest=latest() if callable(latest) else latest
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    state=st.session_state.get(key)
    if state is None or state.get('credential')!=credential:
        try:
            if repo and reader:
                with connect(repo,reader) as storage:book,sha=load(storage,branch)
            else:book,sha=empty_book(),None
        except Exception:
            st.error('The private account could not be read. Pending browser entries remain unchanged. Use Refresh to retry.');return
        state={'book':book,'sha':sha,'refresh':refresh_version,'credential':credential,'last_sync':datetime.now(timezone.utc).strftime('%d %b %H:%M UTC') if repo and reader and sha else None}
        st.session_state[key]=state
    if state['refresh']!=refresh_version:
        try:
            if repo and reader:
                with connect(repo,reader) as storage:state['book'],state['sha']=load(storage,branch)
                state['last_sync']=datetime.now(timezone.utc).strftime('%d %b %H:%M UTC') if state['sha'] else None
        except Exception:st.warning('Account reload failed. Previously loaded records and browser entries are retained.')
        state['refresh']=refresh_version
    book=state['book']
    earliest=min((pd.Timestamp(t['date']).date() for t in book['trades']),default=pd.Timestamp(latest).date())
    fixed=loader(min(earliest,pd.Timestamp(latest).date()-pd.Timedelta(days=400)),latest)
    version=(data_version() if callable(data_version) else data_version,refresh_version,len(fixed),str(fixed.quote_day.min()),str(fixed.quote_day.max()))
    # Holidays do not manufacture a zero "latest daily P&L". An executed newer
    # trade does create a valuation day, with actual prior quote dates retained.
    asof=max(pd.Timestamp(latest),max((pd.Timestamp(t['date']) for t in book['trades']),default=pd.Timestamp(latest)))
    asof=min(asof,pd.Timestamp(today))
    catalog=contract_catalog(fixed,book['trades'],today)
    positions,history,detail=valued(account,book['trades'],asof,version,fixed)
    status,_=risk_state(positions,book['settings']['loss_limit'])
    st.subheader('Account Overview')
    values=history.iloc[-1] if len(history) else {c:0.0 for c in ('Total P&L','Daily P&L','Realized P&L','Unrealized P&L')}
    for col,label in zip(st.columns(4),('Total P&L','Daily P&L','Realized P&L','Unrealized P&L')):
        value=values[label];color=UP if pd.notna(value) and value>0 else DOWN if pd.notna(value) and value<0 else FLAT
        text=f'${value:+,.2f}' if pd.notna(value) else 'Unavailable'
        col.markdown(f'**{label}**<div style="font-size:1.65rem;color:{color}">{text}</div>',unsafe_allow_html=True)
    st.caption(f'Valuation date {asof:%d %b %Y} · Risk {status} · Account loss limit ${book["settings"]["loss_limit"]:,.0f}')
    stale=positions[positions['Net Position'].abs().gt(1e-9)&positions['Quote Date'].lt(asof)] if len(positions) else positions
    if len(stale):st.warning(f'{len(stale)} open contract(s) use prior quotes. Values are indicative at those marks; actual quote dates are shown below.')
    if status=='Red':st.error('Loss limit reached. Only reductions and closes are allowed; reversals are blocked.')
    elif status=='Yellow':st.warning('Account loss has reached 80% of the limit.')
    elif status=='Unknown':st.warning('Valuation is incomplete. Only reductions and closes are allowed until missing quotes or settlement are resolved.')
    settings_col,filter_col=st.columns([1,3])
    with settings_col:
        limit=st.number_input('Loss limit (USD)',min_value=1.0,value=float(book['settings']['loss_limit']),step=1000.0,key=key+'_limit_'+str(book['settings']['loss_limit']))
    filters=filter_col.toggle('Filters',key=key+'_filters')
    ships=contracts=[];start=history.Date.min().date() if len(history) else asof.date();end=asof.date()
    if filters:
        a,b=st.columns(2)
        ships=multi('Vessel',sorted({t['vessel'] for t in book['trades']}),key+'_vessel',[],4,container=a)
        labels={t['contract']:f'{t["vessel"]} {t["label"]} · {t["product"]}' for t in book['trades']}
        ids=[k for k in labels if not ships or next(t for t in book['trades'] if t['contract']==k)['vessel'] in ships]
        contracts=multi('Contract',ids,key+'_contract',[],max(1,len(ids)),container=b,format_func=labels.get)
        dates=st.date_input('Display / export dates',value=(start,end),min_value=start,max_value=end,key=key+'_dates')
        if len(dates)==2:start,end=dates
        elif len(dates)==1:start=end=dates[0]
    selected=[t for t in book['trades'] if (not ships or t['vessel'] in ships) and (not contracts or t['contract'] in contracts)]
    current,full,percontract=valued(account,selected,end,version,fixed)
    if len(percontract):
        percontract=percontract.sort_values(['Contract ID','Date'])
        percontract['Daily P&L']=percontract.groupby('Contract ID')['Total P&L'].diff()
        firsts=percontract.groupby('Contract ID').head(1).index
        percontract.loc[firsts,'Daily P&L']=percontract.loc[firsts,'Total P&L']
        current=current.merge(percontract[percontract.Date.eq(pd.Timestamp(end))][['Contract ID','Daily P&L']],on='Contract ID',how='left')
    st.subheader('Open Positions')
    opened=current[current['Net Position'].abs().gt(1e-9)].copy() if len(current) else current
    if len(opened):
        opened['Contract']=opened.apply(lambda r:f'{r.Vessel} {r.Contract}',axis=1)
        opened['Long/Short']=opened['Net Position'].map(lambda v:'Long' if v>0 else 'Short')
        opened['Quantity']=opened['Net Position'].abs()
        opened=opened.rename(columns={'BFA':'Latest BFA'})
        columns=['Contract','Long/Short','Quantity','Average Cost','Latest BFA','Daily P&L','Unrealized P&L','Quote Date','Status']
        st.dataframe(styled(opened[columns]),hide_index=True,width='stretch')
    else:st.caption('No open positions.')
    st.subheader('Trade Entry')
    last_quantity=book['trades'][-1]['days'] if book['trades'] else 5
    common={'account':account,'session':state.setdefault('session',uuid.uuid4().hex),'today':str(today),'trades':book['trades'],'catalog':list(catalog.values()),'writable':bool(repo and reader and writer and not synthetic),
            'last_quantity':last_quantity,'last_sync':state.get('last_sync'),'ack':state.get('ack'),'failure':state.get('failure'),
            'settings_change':{'base':book['settings']['loss_limit'],'value':float(limit)} if limit!=book['settings']['loss_limit'] else None}
    entry=grid(data={**common,'mode':'entry','visible':[]},key=key+'_entry',default={'batch':None},on_batch_change=lambda:None)
    st.subheader('P&L Performance')
    shown=full[full.Date.between(pd.Timestamp(start),pd.Timestamp(end))] if len(full) else full
    if len(shown):
        fig=chart('USD',height=330);fig.add_scatter(x=shown.Date,y=shown['Total P&L'],name='Cumulative P&L',line_color=UP,connectgaps=False)
        fig.add_hline(y=0,line_width=1);date_axis(fig,(pd.Timestamp(start),pd.Timestamp(end)));display(fig,key+'_cumulative')
        fig=chart('USD',height=300);bars(fig,shown.set_index('Date').rename(columns={'Daily P&L':'change'}),'Daily P&L');date_axis(fig,(pd.Timestamp(start),pd.Timestamp(end)));display(fig,key+'_daily')
        rank=current.sort_values('Total P&L');fig=chart(None,height=max(260,35*len(rank)))
        fig.add_bar(y=[f'{r.Vessel} {r.Contract}' for _,r in rank.iterrows()],x=rank['Total P&L'],orientation='h',marker_color=[UP if v>=0 else DOWN for v in rank['Total P&L']])
        fig.update_xaxes(title='Contract P&L · USD');display(fig,key+'_ranking')
    else:st.caption('Performance appears after the first confirmed transaction.')
    st.subheader('Trade History')
    visible=[t['id'] for t in reversed(selected) if pd.Timestamp(start)<=pd.Timestamp(t['date'])<=pd.Timestamp(end)]
    if not filters:visible=visible[:50]
    grid(data={**common,'mode':'history','visible':visible},key=key+'_history',default={'batch':None},on_batch_change=lambda:None)
    download=lambda trades=deepcopy(book['trades']),quotes=fixed,a=start,b=end,v=tuple(ships),c=tuple(contracts):report(trades,quotes,a,b,v,c)
    st.download_button('Export Excel',download,file_name=f'FFA_Report_{end}.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',key=key+'_download',on_click='ignore')
    request=entry.batch
    if request and request.get('version')!=state.get('ack',{}).get('version'):
        event=(request.get('version'),request.get('attempt'))
        if state.get('handled')!=event:
            state['handled']=event
            if common['writable']:
                try:
                    with connect(repo,writer) as storage:saved,sha,batch_id=sync_batch(storage,request['payload'],catalog,fixed,max(asof,pd.Timestamp(today)),branch)
                    state.update(book=saved,sha=sha,last_sync=datetime.now(timezone.utc).strftime('%d %b %H:%M:%S UTC'),ack={'version':request['version'],'batch_id':batch_id})
                    state.pop('failure',None)
                except Exception as exc:
                    message=str(exc) if isinstance(exc,(ValueError,Conflict)) else 'Private storage is unavailable. The browser batch is retained and will retry automatically.'
                    state['failure']={'version':request['version'],'attempt':request.get('attempt'),'message':message}
                    if isinstance(exc,Conflict):
                        try:
                            with connect(repo,reader) as storage:state['book'],state['sha']=load(storage,branch)
                        except Exception:pass
                rerender()
