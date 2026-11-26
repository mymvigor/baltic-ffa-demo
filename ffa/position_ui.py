"""Compact position workspace. Draft edits are local until an explicit Save."""
from copy import deepcopy
from datetime import date
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
from io import BytesIO
import uuid
import pandas as pd
import streamlit as st
from .positions import empty_book,calculate,risk_state,permits,replace_trade
from .position_store import load,save,versions,DEFAULT_BRANCH
from .position_excel import metadata,infer_columns,preview,report
from .store import connect
from .preferences import choice,multi,calendar
from .selection import contract_label
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
        reader=st.secrets.get('positions_reader_token',st.secrets.get('github_reader_token'))
        writer=st.secrets.get('positions_writer_token')
        return repo,branch,reader,writer
    except FileNotFoundError:return None,DEFAULT_BRANCH,None,None


def styled(table):
    fmt={c:'{:+,.2f}' for c in table if 'P&L' in c or c=='Net Position'}
    fmt.update({c:'{:,.2f}' for c in ('Average Cost','BFA') if c in table})
    for c in ('Date','Quote Date'):
        if c in table:fmt[c]=lambda d:pd.Timestamp(d).strftime('%d %b %Y') if pd.notna(d) else '—'
    columns=[c for c in table if 'P&L' in c or c=='Net Position']
    value=table.style.format(fmt,na_rep='—')
    return value.map(signed_style,subset=columns) if columns else value


@st.cache_data(max_entries=16,show_spinner=False)
def valued(account,trades,asof,version,_fixed):
    return calculate(trades,_fixed,asof)


def position_management(loader, latest, refresh, data_version=None, synthetic=False):
    st.title('Position Management')
    repo,branch,reader,writer=config()
    if synthetic:
        repo=reader=writer=None
        st.caption('Synthetic market quotes · session-only simulation. Private account records are not loaded or written.')
    account=f'{repo}:{branch}:'+hashlib.sha256((reader or '').encode()).hexdigest()[:12]
    key='_positions_'+account
    refresh_version=refresh('positions')
    latest=latest() if callable(latest) else latest
    if key not in st.session_state:
        if repo and reader:
            try:
                with st.spinner('Loading private account…'):
                    with connect(repo,reader) as store:book,sha=load(store,branch)
            except Exception:
                st.error('The private ledger could not be read. Trading is disabled to avoid replacing existing records. Use Refresh to retry.');return
        else:book,sha=empty_book(),None
        st.session_state[key]={'base':deepcopy(book),'draft':book,'sha':sha,'refresh':refresh_version}
    state=st.session_state[key]
    if state.pop('reset_limit',False):st.session_state.pop(key+'_limit',None)
    if state.get('refresh')!=refresh_version:
        if repo and reader:
            try:
                with connect(repo,reader) as store:fresh,sha=load(store,branch)
                if state['draft']==state['base']:state.update(base=deepcopy(fresh),draft=fresh,sha=sha)
                else:st.info('Quotes refreshed. Your unsaved draft is retained; Save checks for concurrent changes.')
            except Exception:st.warning('Account reload failed. The current draft is retained.')
        state['refresh']=refresh_version
    book=state['draft']
    if not repo or not reader:st.info('Session-only draft. Configure private position storage before relying on saved trades.')
    with st.popover('Account settings'):
        limit=st.number_input('Account loss limit (USD)',min_value=1.0,value=float(book['settings'].get('loss_limit',25000)),step=1000.0,key=key+'_limit')
        if st.button('Apply limit to draft',key=key+'_limit_apply'):
            book['settings']['loss_limit']=float(limit)
        historical=st.checkbox('Include older contracts',key=key+'_older',help='Load the full quote archive for historical trade entry and import.')
        if repo and reader and st.button('Load saved versions',key=key+'_versions'):
            try:
                with connect(repo,reader) as store:state['versions']=versions(store,branch)
            except Exception:st.warning('Could not load account versions.')
        saved_versions=state.get('versions',[])
        if saved_versions:
            version=st.selectbox('Saved version',saved_versions,format_func=lambda v:v['date']+' · '+v['sha'][:7],key=key+'_version')
            if st.button('Restore version to draft',key=key+'_restore'):
                try:
                    with connect(repo,reader) as store:restored,_=load(store,branch,ref=version['sha'])
                    state['pending_restore']=restored
                except Exception:st.warning('Could not restore the selected version.')
        if st.button('Discard draft and reload',key=key+'_discard'):
            # Explicit destructive draft action; the persisted ledger is never changed here.
            del st.session_state[key];rerender()
    earliest=min((pd.Timestamp(t['date']).date() for t in book['trades']),default=pd.Timestamp(latest).date())
    with st.spinner('Loading contract quotes…'):
        fixed=loader(None if historical else min(earliest,pd.Timestamp(latest).date()-pd.Timedelta(days=400)),latest)
    version=(data_version() if callable(data_version) else data_version,refresh_version,historical,str(earliest),len(fixed),str(fixed.quote_day.min()),str(fixed.quote_day.max()))
    asof=max(pd.Timestamp(latest),pd.Timestamp(datetime.now(ZoneInfo('Asia/Shanghai')).date()))
    # A future-dated upload is not an executed trade or a valuation input.
    if any(pd.Timestamp(t['date'])>asof for t in book['trades']):st.warning('Future-dated records are excluded until their trade date.')
    pending=state.pop('pending_restore',None)
    if pending is not None:
        try:
            ok,reason=permits(book['trades'],pending['trades'],fixed,asof,book['settings']['loss_limit'])
            if not ok:st.warning(reason)
            else:
                state['draft']=pending;state['reset_limit']=True
                rerender()
        except ValueError as exc:st.warning(str(exc))
    try:positions,history,detail=valued(account,book['trades'],asof,version,fixed)
    except ValueError as exc:st.error(str(exc));return
    status,total=risk_state(positions,book['settings']['loss_limit'])
    if status=='Red':st.error('Loss limit reached. New risk is blocked; reductions and closes remain available.')
    elif status=='Yellow':st.warning('Account loss has reached 80% of the loss limit.')
    elif status=='Unknown':st.warning('Account valuation is incomplete. New risk is blocked until missing quotes or settlement are resolved.')
    dirty=book!=state['base']
    save_area,export_area=st.columns(2)
    with save_area:
        if st.button('Save to GitHub',key=key+'_save',disabled=not dirty or not writer,help='Explicitly save account transactions and settings to the private positions branch.'):
            try:
                def validate(remote,merged):
                    ok,reason=permits(remote['trades'],merged['trades'],fixed,asof,min(remote['settings']['loss_limit'],merged['settings']['loss_limit']))
                    if not ok:raise ValueError(reason)
                with st.spinner('Saving private account…'):
                    with connect(repo,writer) as store:saved,sha=save(store,state['base'],book,branch,validate)
                state.update(base=deepcopy(saved),draft=saved,sha=sha);st.success('Saved.');rerender()
            except Exception as exc:
                if isinstance(exc,ValueError):st.error(str(exc))
                else:st.error('Save failed or conflicted. Your draft is retained. Reload saved versions or reconcile before retrying.')
        if dirty:st.caption('Unsaved draft' + (' · positions_writer_token is not configured.' if not writer else ''))
    view=choice('Position view',['Overview','Positions','Transactions'],key+'_view','Overview',control='segmented_control')
    ships=multi('Vessel',sorted({t['vessel'] for t in book['trades']}),key+'_ships',[],4)
    ids=sorted({t['contract'] for t in book['trades'] if not ships or t['vessel'] in ships})
    labels={t['contract']:t['vessel']+' '+t.get('label',t['contract'])+' · '+t['product'] for t in book['trades']}
    selected=multi('Contract',ids,key+'_contracts',[],max(1,len(ids)),format_func=labels.get)
    start_default=history.Date.min().date() if len(history) else asof.date()
    date_key=key+('_empty' if not book['trades'] else '')
    a,b=st.columns(2)
    start=calendar('From',date_key+'_start',start_default,start_default,asof.date(),container=a)
    end=calendar('To',date_key+'_end',asof.date(),start_default,asof.date(),container=b)
    if start>end:st.info('Choose a start date before the end date.');return
    filtered=[t for t in book['trades'] if (not ships or t['vessel'] in ships) and (not selected or t['contract'] in selected)]
    current,full,percontract=valued(account,filtered,end,version,fixed)
    shown=full[full.Date.between(pd.Timestamp(start),pd.Timestamp(end))] if len(full) else full
    with export_area:
        download=lambda trades=deepcopy(book['trades']),quotes=fixed,a=start,b=end,v=tuple(ships),c=tuple(selected):report(trades,quotes,a,b,v,c)
        st.download_button('Export Excel',download,file_name=f'FFA_Report_{end}.xlsx',
                           mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',key=key+'_download',on_click='ignore')
    if view=='Overview':
        if full.empty:st.info('Add a trade in Transactions to start.');return
        last=full.iloc[-1];opening=full[full.Date.lt(pd.Timestamp(start))]
        baseline=float(opening['Total P&L'].iloc[-1]) if len(opening) else 0.0
        metrics=[('Total P&L',last['Total P&L']),('Period P&L',last['Total P&L']-baseline),('Daily P&L',last['Daily P&L']),('Gross Days',last['Gross Days'])]
        for col,(label,value) in zip(st.columns(4),metrics):
            color=UP if pd.notna(value) and value>0 else DOWN if pd.notna(value) and value<0 else FLAT
            text=f'{value:+,.2f}' if pd.notna(value) else 'Unavailable'
            col.markdown(f'**{label}**<div style="font-size:1.65rem;color:{color}">{text}</div>',unsafe_allow_html=True)
        st.caption(f'Realized {last["Realized P&L"]:+,.2f} · Unrealized {last["Unrealized P&L"]:+,.2f} · Net days {last["Net Position"]:+,.2f}')
        st.caption(f'Account risk: {status} · Loss limit ${book["settings"]["loss_limit"]:,.0f}. Overview follows display filters; risk always uses the whole account.')
        if not shown.empty:
            fig=chart('USD',height=330);fig.add_scatter(x=shown.Date,y=shown['Total P&L'],name='Cumulative P&L',line_color=UP,connectgaps=False)
            fig.add_hline(y=0,line_width=1);date_axis(fig,(pd.Timestamp(start),pd.Timestamp(end)));display(fig,key+'_cumulative')
            fig=chart('USD',height=280);bars(fig,shown.set_index('Date').rename(columns={'Daily P&L':'change'}),'Daily P&L');date_axis(fig,(pd.Timestamp(start),pd.Timestamp(end)));display(fig,key+'_daily')
            rank=current.sort_values('Total P&L');fig=chart('USD',height=max(260,35*len(rank)))
            fig.update_xaxes(title='P&L · USD');fig.update_yaxes(title=None)
            fig.add_bar(y=[labels.get(k,k) for k in rank['Contract ID']],x=rank['Total P&L'],orientation='h',marker_color=[UP if v>=0 else DOWN for v in rank['Total P&L']]);display(fig,key+'_ranking')
    elif view=='Positions':
        if current.empty:st.info('No positions or trades match these filters.')
        else:
            st.caption('P&L and cost include all earlier transactions. Prior quotes retain their actual dates; pending settlements remain unvalued.')
            st.dataframe(styled(current.drop(columns=['Date','Contract ID'])),hide_index=True,width='stretch')
    else:
        catalog=fixed[fixed.kind.eq('BFA')].sort_values('quote_day').drop_duplicates('identity',keep='last')
        # Preserve metadata for old trades even if an archive slice is temporarily missing.
        trade_editor(book,state,catalog,fixed,asof,key)
        table=pd.DataFrame([t for t in filtered if pd.Timestamp(start)<=pd.Timestamp(t['date'])<=pd.Timestamp(end)])
        if len(table):st.dataframe(table.reindex(columns=['date','vessel','product','label','side','price','days','fees','notes']),hide_index=True,width='stretch')
        import_editor(book,state,catalog,fixed,asof,key)


def trade_editor(book,state,catalog,fixed,asof,key):
    if st.button('+ Trade',key=key+'_add'):state['editing']='new'
    if book['trades']:
        with st.popover('Edit transaction'):
            picks={t['id']:t for t in book['trades']}
            chosen=st.selectbox('Transaction',list(picks),format_func=lambda k:f'{picks[k]["date"]} · {picks[k]["vessel"]} {picks[k].get("label","")} · {picks[k]["side"]} {picks[k]["days"]} @ {picks[k]["price"]}',key=key+'_edit_id')
            if st.button('Edit',key=key+'_edit'):state['editing']=chosen
    editing=state.get('editing')
    if editing is None:return
    old=next((t for t in book['trades'] if t['id']==editing),None)
    if catalog.empty:st.info('No fixed contracts have been loaded.');return
    choices=list(catalog.identity)
    labels={r.identity:f'{r.vessel} {r["product"]} · {contract_label(r)} · {r.currency}/{r.unit}' for _,r in catalog.iterrows()}
    order=catalog.assign(expired=catalog.delivery_end.lt(asof)).sort_values(['expired','delivery_start','vessel'])
    choices=list(order.identity)
    if old and old['contract'] not in choices:choices.insert(0,old['contract']);labels[old['contract']]=old.get('label',old['contract'])
    editor_key=key+'_form_'+editing
    with st.form(editor_key):
        contract=st.selectbox('Contract',choices,index=choices.index(old['contract']) if old else 0,format_func=labels.get)
        trade_date=st.date_input('Trade Date',value=pd.Timestamp(old['date']).date() if old else asof.date(),max_value=asof.date())
        side=st.segmented_control('Buy/Sell',['Buy','Sell'],default=old['side'] if old else 'Buy',selection_mode='single')
        a,b=st.columns(2)
        price=a.number_input('Price',min_value=0.0,value=float(old['price']) if old else 0.0,step=100.0)
        days=b.number_input('Days',min_value=.01,value=float(old['days']) if old else 1.0,step=1.0)
        fees=st.number_input('Fees (USD)',min_value=0.0,value=float(old.get('fees',0)) if old else 0.0)
        notes=st.text_input('Notes',value=old.get('notes','') if old else '')
        submitted=st.form_submit_button('Apply to draft')
        cancel=st.form_submit_button('Cancel')
    if cancel:state.pop('editing',None);rerender()
    if submitted:
        try:
            row=catalog[catalog.identity.eq(contract)]
            info=metadata(row.iloc[0]) if len(row) else {k:old[k] for k in ('contract','vessel','product','currency','unit','tenor','delivery_start','delivery_end','label')}
            trade=dict(info,id=old['id'] if old else uuid.uuid4().hex,date=str(trade_date),side=side,price=price,days=days,fees=fees,notes=notes,
                       sequence=old.get('sequence',0) if old else max((t.get('sequence',0) for t in book['trades']),default=0)+1)
            candidate=replace_trade(book,trade)
            ok,reason=permits(book['trades'],candidate['trades'],fixed,asof,book['settings']['loss_limit'])
            if not ok:st.warning(reason)
            else:state['draft']=candidate;state.pop('editing',None);state.pop('export',None);rerender()
        except ValueError as exc:st.warning(str(exc))


def import_editor(book,state,catalog,fixed,asof,key):
    with st.expander('Import Excel'):
        uploaded=st.file_uploader('Transaction workbook',type=['xlsx'],key=key+'_import')
        if uploaded is None:return
        if uploaded.size>20*1024*1024:st.warning('Maximum workbook size is 20 MB.');return
        try:
            workbook=pd.ExcelFile(BytesIO(uploaded.getvalue()),engine='openpyxl')
            sheet=st.selectbox('Sheet',workbook.sheet_names,key=key+'_sheet')
            table=workbook.parse(sheet);mapping=infer_columns(table)
            with st.popover('Column mapping'):
                for field in mapping:
                    options=[None,*table.columns]
                    mapping[field]=st.selectbox(field.title(),options,index=options.index(mapping[field]),format_func=lambda c:'Not used' if c is None else str(c),key=key+'_map_'+field+'_'+hashlib.sha256(uploaded.getvalue()).hexdigest()[:8])
            trader=None
            if mapping['trader']:trader=st.selectbox('Import trader',sorted(table[mapping['trader']].dropna().astype(str).unique()),key=key+'_trader')
            vessel=st.selectbox('Default vessel',list(catalog.vessel.unique()),key=key+'_import_vessel') if not mapping['vessel'] else None
            products=list(catalog.loc[catalog.vessel.eq(vessel),'product'].unique()) if vessel else list(catalog['product'].unique())
            product=st.selectbox('Default benchmark',products,key=key+'_import_product') if not mapping['product'] else None
            trades,errors,duplicates=preview(table,mapping,catalog,book['trades'],trader,vessel,product)
            if duplicates and st.checkbox('These matching rows are separate executed fills; include duplicates',key=key+'_keep_duplicates'):
                trades,errors,duplicates=preview(table,mapping,catalog,book['trades'],trader,vessel,product,allow_duplicates=True)
            future=[t for t in trades if pd.Timestamp(t['date'])>asof]
            if future:errors.append('Future trade dates are not accepted.')
            st.caption(f'{len(trades)} valid new trades · {duplicates} duplicates excluded · {len(errors)} errors. Same-day order follows workbook row order.')
            for error in errors[:5]:st.warning(error)
            if st.button('Import to draft',key=key+'_import_apply',disabled=not trades or bool(errors)):
                candidate=deepcopy(book);candidate['trades'].extend(trades)
                ok,reason=permits(book['trades'],candidate['trades'],fixed,asof,book['settings']['loss_limit'])
                if not ok:st.warning(reason)
                else:state['draft']=candidate;state.pop('export',None);rerender()
        except Exception:st.warning('Workbook could not be read or matched. Check sheet, date cells and column mapping.')
