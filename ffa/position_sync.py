"""Validated batch edits. No incomplete row enters the authoritative ledger."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
import math
import pandas as pd
from .positions import ordered,validate_trade,permits
from .position_excel import metadata
from .position_store import Conflict,load,save
from .selection import contract_label

FIELDS=('Trade Date','Contract','Side','Price','Quantity','Fees','Notes')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def contract_catalog(fixed,trades=(),today=None):
    rows=fixed[fixed.kind.eq('BFA') & fixed.delivery_start.notna() & fixed.delivery_end.notna()].sort_values('quote_day').drop_duplicates('identity',keep='last')
    result={}
    for _,row in rows.iterrows():
        info=metadata(row)
        # A Quantity in days already is the TC exposure. Do not multiply it by
        # the number of calendar/settlement days or assume an exchange lot size.
        info.update(quantity_unit='days' if row.currency=='USD' and row.unit=='day' else None,
                    multiplier=1.0 if row.currency=='USD' and row.unit=='day' else None,
                    search=f'{row.vessel} {contract_label(row).split(" · ")[0]} · {row["product"]} · {row.currency}/{row.unit}')
        result[row.identity]=info
    for t in trades:
        if t['contract'] not in result:
            result[t['contract']]={k:t.get(k) for k in ('contract','vessel','product','currency','unit','tenor','delivery_start','delivery_end','label')}
            result[t['contract']].update(quantity_unit='days' if t['currency']=='USD' and t['unit']=='day' else None,
                                         multiplier=1.0 if t['currency']=='USD' and t['unit']=='day' else None,
                                         search=f'{t["vessel"]} {t.get("label",t["contract"]).split(" · ")[0]} · {t["product"]} · {t["currency"]}/{t["unit"]}')
    counts={}
    for t in trades:counts[t['contract']]=counts.get(t['contract'],0)+1
    today=pd.Timestamp(today or pd.Timestamp.today().date())
    quote_dates={row.identity:pd.Timestamp(row.quote_day).toordinal() for _,row in rows.iterrows()}
    tenor_order={'month':0,'quarter':1,'year':2}
    def priority(pair):
        identity,info=pair;expired=pd.Timestamp(info['delivery_end'])<today
        delivery=pd.Timestamp(info['delivery_start']).toordinal()
        return (expired,tenor_order.get(info['tenor'],3),-quote_dates.get(identity,0),-delivery if expired else delivery,-counts.get(identity,0),info['search'])
    return dict(sorted(result.items(),key=priority))


def raw_trade(t,catalog):
    return {'Trade Date':t['date'],'Contract':catalog[t['contract']]['search'],'Side':t['side'],'Price':t['price'],
            'Quantity':t['days'],'Fees':t.get('fees',0),'Notes':t.get('notes','')}


def resolve_contract(text,catalog):
    text=str(text).strip().casefold()
    exact=[c for c in catalog.values() if text in (c['contract'].casefold(),c['search'].casefold())]
    if not exact:
        normalize=lambda x:''.join(c for c in x.upper() if c.isalnum())
        code=normalize(text)
        exact=[c for c in catalog.values() if code==normalize(c['vessel']+' '+c['label'].split(' · ')[0])]
    if len(exact)!=1:raise ValueError('Select an unambiguous fixed contract including vessel and benchmark.')
    if exact[0]['quantity_unit'] is None or exact[0]['multiplier'] is None:
        raise ValueError('The quantity unit or contract multiplier is unverified for this contract. Days-based TC contracts are supported.')
    return exact[0]


def build_batch(book,payload,catalog,today):
    if not isinstance(payload,dict) or not isinstance(payload.get('edits',{}),dict) or not isinstance(payload.get('deletes',{}),dict):
        raise ValueError('Invalid pending batch.')
    if len(payload.get('edits',{}))+len(payload.get('deletes',{}))>2000:raise ValueError('Batch exceeds 2,000 rows.')
    candidate=deepcopy(book);current={t['id']:t for t in book['trades']};byid=deepcopy(current);errors=[]
    sequence=max((int(t.get('sequence',0)) for t in book['trades']),default=0)
    changes=sorted(payload.get('edits',{}).items(),key=lambda p:(p[1].get('order',0),p[0]))
    for index,(identity,edit) in enumerate(changes,1):
        try:
            values=edit['fields'];base=edit.get('base');info=resolve_contract(values.get('Contract',''),catalog)
            d=pd.Timestamp(values.get('Trade Date'))
            if pd.isna(d) or d.tzinfo or d!=d.normalize() or d>pd.Timestamp(today):raise ValueError('Use a valid, non-future Trade Date.')
            number=lambda k,default=None:float(str(values.get(k,default) if values.get(k,default) not in ('',None) else default).replace(',','').replace('$',''))
            trade={k:info[k] for k in ('contract','vessel','product','currency','unit','tenor','delivery_start','delivery_end','label')}
            if base:
                # Preserve migration provenance, execution order and original IDs.
                trade={**base,**trade}
            trade.update(id=str(identity),date=str(d.date()),side=str(values.get('Side','')).title(),price=number('Price'),
                         days=number('Quantity'),fees=number('Fees',0),notes=str(values.get('Notes','') or ''),
                         sequence=base.get('sequence',0) if base else sequence+index,
                         quantity_unit=info['quantity_unit'],multiplier=info['multiplier'])
            validate_trade(trade)
            if current.get(identity) not in (base,trade):raise Conflict(f'{trade["date"]} {info["search"]}: changed remotely. Undo the pending edit before editing the latest record.')
            byid[identity]=trade
        except Conflict:raise
        except (ValueError,TypeError,KeyError) as exc:errors.append(f'Row {index}: {exc}')
    for identity,base in payload.get('deletes',{}).items():
        if current.get(identity) not in (None,base):raise Conflict('A transaction marked for deletion changed remotely. Undo the deletion to review it.')
        byid.pop(identity,None)
    settings=payload.get('settings')
    if settings:
        current_limit=book['settings']['loss_limit'];value=float(settings['value'])
        if not math.isfinite(value) or value<=0:errors.append('Loss limit must be positive.')
        if current_limit not in (settings['base'],value):raise Conflict('The loss limit changed in another session.')
        candidate['settings']['loss_limit']=value
    if errors:raise ValueError('\n'.join(errors[:10]))
    candidate['trades']=ordered(list(byid.values()))
    return candidate


def validate_changes(before,after,fixed,asof,limit):
    """Check each change in execution order, so a batch cannot hide reversals."""
    staged=deepcopy(before);a={t['id']:t for t in before};b={t['id']:t for t in after}
    events=[(t,False) for t in after if a.get(t['id'])!=t]+[(t,True) for t in before if t['id'] not in b]
    events.sort(key=lambda event:(pd.Timestamp(event[0]['date']),event[0].get('sequence',0),event[0]['id']))
    for trade,deleting in events:
        new=[t for t in staged if t['id']!=trade['id']]
        if not deleting:new.append(trade)
        ok,reason=permits(staged,new,fixed,asof,limit)
        if not ok:raise ValueError(reason)
        staged=new


def sync_batch(store,payload,catalog,fixed,asof,branch):
    batch_id='edit-'+digest(payload)
    remote,sha=load(store,branch)
    if batch_id in remote.get('batches',{}):return remote,sha,batch_id
    local=build_batch(remote,payload,catalog,asof)
    if local==remote:return remote,sha,batch_id
    validate_changes(remote['trades'],local['trades'],fixed,asof,min(remote['settings']['loss_limit'],local['settings']['loss_limit']))
    local.setdefault('batches',{})[batch_id]={'saved_at':datetime.now(timezone.utc).isoformat(),'kind':'edit'}
    original={t['id'] for t in remote['trades']}
    new_order=[t['id'] for t in local['trades'] if t['id'] not in original]
    def validate(latest,merged):
        ids={t['id'] for t in latest['trades']};seq=max((int(t.get('sequence',0)) for t in latest['trades']),default=0)
        for identity in new_order:
            if identity in ids:continue
            seq+=1
            next(t for t in merged['trades'] if t['id']==identity)['sequence']=seq
        validate_changes(latest['trades'],merged['trades'],fixed,asof,min(latest['settings']['loss_limit'],merged['settings']['loss_limit']))
    result,sha=save(store,remote,local,branch,validate,batch_id=batch_id)
    return result,sha,batch_id
