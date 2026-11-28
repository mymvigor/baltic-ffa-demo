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
from .position_sync import contract_catalog,sync_batch,POSITION_VESSELS
from .position_excel import report
from .position_grid import grid
from .position_pnl import contract_histories,account_pnl,contract_pnl
from .store import connect
from .quote_views import UP,DOWN,FLAT,signed_style


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


def display_filters(trades,start,end,key):
    """Filter actual saved trades; an empty ledger has no unusable selectors."""
    if not trades:
        st.caption('No saved transactions in this account. Filters will appear after trades are saved or migrated.')
        return [],[],start,end,False
    vessel=st.segmented_control('Vessel',['All',*POSITION_VESSELS],default='All',key=key+'_display_vessel') or 'All'
    ships=[] if vessel=='All' else [vessel]
    available=[t for t in trades if t['vessel'] in POSITION_VESSELS and (not ships or t['vessel'] in ships)]
    if not available:
        st.caption(f'No saved transactions for {vessel}.')
        return ships,[],start,end,True
    labels={t['contract']:f'{t["vessel"]} {t["label"]} · {t["product"]}' for t in available}
    ids=list(labels)
    contract_key=key+'_display_contracts'
    previous=st.session_state.get(contract_key,[])
    valid=[identity for identity in previous if identity in labels]
    if previous!=valid:st.session_state[contract_key]=valid
    a,b=st.columns([3,1])
    contracts=a.multiselect('Contracts',ids,key=contract_key,format_func=labels.get,placeholder='All contracts')
    period=b.selectbox('Period',['All dates','Custom dates'],key=key+'_display_period')
    if period=='Custom dates':
        date_key=key+'_display_dates'
        remembered=st.session_state.get(date_key)
        if remembered and any(not start<=d<=end for d in remembered):
            st.session_state[date_key]=tuple(min(end,max(start,d)) for d in remembered)
        dates=st.date_input('Display / export dates',value=(start,end),min_value=start,max_value=end,key=date_key)
        if len(dates)==2:start,end=dates
        elif len(dates)==1:start=end=dates[0]
    return ships,contracts,start,end,vessel!='All' or bool(contracts) or period!='All dates'


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
    paths=contract_histories(detail)
    if paths:
        positions=positions.copy()
        positions['Daily P&L']=positions['Contract ID'].map({identity:path['Daily P&L'].iloc[-1] for identity,path in paths.items()})
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
    limit=st.columns([1,3])[0].number_input('Loss limit (USD)',min_value=1.0,value=float(book['settings']['loss_limit']),step=1000.0,key=key+'_limit_'+str(book['settings']['loss_limit']))
    st.subheader('Open Positions')
    opened=positions[positions['Net Position'].abs().gt(1e-9)].copy() if len(positions) else positions
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
            'last_quantity':last_quantity,'last_sync':state.get('last_sync'),
            'settings_change':{'base':book['settings']['loss_limit'],'value':float(limit)} if limit!=book['settings']['loss_limit'] else None}
    entry_catalog=[c for c in catalog.values() if c['vessel'] in POSITION_VESSELS]
    acks=state.setdefault('acks',{});failures=state.setdefault('failures',{})
    entry=grid(data={**common,'ack':acks.get('entry'),'failure':failures.get('entry'),'catalog':entry_catalog,'mode':'entry','visible':[]},key=key+'_entry',default={'batch':None},on_batch_change=lambda:None)
    account_pnl(history,positions,key)
    contract_pnl(paths,book['trades'],key)
    st.subheader('Trade History')
    first=history.Date.min().date() if len(history) else asof.date()
    st.caption('Filters apply to trade history and Excel export. Account and contract P&L always show the full ledger.')
    ships,contracts,start,end,filters=display_filters(book['trades'],first,asof.date(),key)
    selected=[t for t in book['trades'] if (not ships or t['vessel'] in ships) and (not contracts or t['contract'] in contracts)]
    visible=[t['id'] for t in reversed(selected) if pd.Timestamp(start)<=pd.Timestamp(t['date'])<=pd.Timestamp(end)]
    if not filters:visible=visible[:50]
    history_grid=grid(data={**common,'ack':acks.get('history'),'failure':failures.get('history'),'mode':'history','visible':visible},key=key+'_history',default={'batch':None},on_batch_change=lambda:None)
    download=lambda trades=deepcopy(book['trades']),quotes=fixed,a=start,b=end,v=tuple(ships),c=tuple(contracts):report(trades,quotes,a,b,v,c)
    st.download_button('Export Excel',download,file_name=f'FFA_Report_{end}.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',key=key+'_download',on_click='ignore')
    handled=state.setdefault('section_handled',{});updated=False
    for scope,result in (('entry',entry),('history',history_grid)):
        request=result.batch
        # Ignore a legacy component's shared batch. A new section-specific
        # Save click is required; no old widget state can trigger a write.
        if not request or request.get('scope')!=scope or request.get('version')==acks.get(scope,{}).get('version'):continue
        event=(request.get('version'),request.get('attempt'))
        if handled.get(scope)!=event:
            handled[scope]=event
            if common['writable']:
                try:
                    with connect(repo,writer) as storage:saved,sha,batch_id=sync_batch(storage,request['payload'],catalog,fixed,max(asof,pd.Timestamp(today)),branch,scope=scope)
                    state.update(book=saved,sha=sha,last_sync=datetime.now(timezone.utc).strftime('%d %b %H:%M:%S UTC'))
                    acks[scope]={'version':request['version'],'batch_id':batch_id}
                    failures.pop(scope,None)
                except Exception as exc:
                    message=str(exc) if isinstance(exc,(ValueError,Conflict)) else 'Private storage is unavailable. Your changes are retained. Click this section’s save button to retry.'
                    failures[scope]={'version':request['version'],'attempt':request.get('attempt'),'message':message}
                    if isinstance(exc,Conflict):
                        try:
                            with connect(repo,reader) as storage:state['book'],state['sha']=load(storage,branch)
                        except Exception:pass
                updated=True
    if updated:rerender()
