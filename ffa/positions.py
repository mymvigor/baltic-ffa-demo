"""Single-account day-based FFA ledger. Pure calculations; no storage side effects."""
from copy import deepcopy
import math
import numpy as np
import pandas as pd
from .settlement import sessions, SUPPORTED

EPS=1e-9


def empty_book():
    return {'schema':1,'trades':[],'settings':{'loss_limit':25000.0}}


def validate_trade(trade):
    required=('id','date','contract','vessel','product','currency','unit','tenor','delivery_start','delivery_end','side','price','days')
    if any(k not in trade or trade[k] is None or str(trade[k]).strip()=='' for k in required):raise ValueError('Missing trade or contract information.')
    d=pd.Timestamp(trade['date'])
    if pd.isna(d) or d.tzinfo is not None or d!=d.normalize():raise ValueError('Trade Date must be a calendar date without time or timezone.')
    start,end=pd.Timestamp(trade['delivery_start']),pd.Timestamp(trade['delivery_end'])
    if pd.isna(start) or pd.isna(end) or start>end or trade['tenor'] not in ('month','quarter','year'):
        raise ValueError('The fixed contract delivery period is invalid or unconfirmed.')
    if trade['side'] not in ('Buy','Sell'):raise ValueError('Side must be Buy or Sell.')
    for k in ('price','days','fees'):
        v=float(trade.get(k,0))
        if not math.isfinite(v) or v<0 or (k=='days' and v<=0):raise ValueError(f'Invalid {k}.')
    if trade['currency']!='USD' or trade['unit']!='day':raise ValueError('Days-based P&L requires USD/day contracts. Other units cannot be treated as days.')
    if d>pd.Timestamp(trade['delivery_end']):raise ValueError('Trade date is after contract expiry.')
    return trade


def ordered(trades):
    seen=set()
    for t in trades:
        validate_trade(t)
        if t['id'] in seen:raise ValueError('Duplicate transaction ID.')
        seen.add(t['id'])
    return sorted(trades,key=lambda t:(pd.Timestamp(t['date']),int(t.get('sequence',0)),t['id']))


def apply_trade(state, trade):
    """Signed days, moving average entry cost, fees immediately realized."""
    old=state['net'];signed=float(trade['days'])*(1 if trade['side']=='Buy' else -1)
    price=float(trade['price']);cost=state['cost'];new=old+signed
    state['realized']-=float(trade.get('fees',0))
    if abs(old)<EPS or old*signed>0:
        state['cost']=(abs(old)*cost+abs(signed)*price)/abs(new)
    else:
        closed=min(abs(old),abs(signed))
        state['realized']+=closed*(price-cost)*(1 if old>0 else -1)
        if abs(new)<EPS:state['cost']=0.0
        elif old*new<0:state['cost']=price
    state['net']=0.0 if abs(new)<EPS else new


def settlement_values(trades, fixed, asof):
    """Only reconstruct monthly TC settlement with a verified full publication calendar."""
    found={}
    for t in trades:
        k=t['contract']
        if k in found or t['tenor']!='month' or t['product'] not in SUPPORTED:continue
        end=pd.Timestamp(t['delivery_end']);cal=sessions(end.to_period('M'))
        if cal is None or pd.Timestamp(asof)<cal[-1]:continue
        spot=fixed[fixed.vessel.eq(t['vessel']) & fixed['product'].eq(t['product']) & fixed.kind.eq('Spot') &
                   fixed.currency.eq(t['currency']) & fixed.unit.eq(t['unit']) & fixed.quote_day.le(pd.Timestamp(asof))]
        s=spot.sort_values('quote_day').drop_duplicates('quote_day',keep='last').set_index('quote_day').price.reindex(cal)
        if len(s) and s.notna().all():found[k]=(cal[-1],float(s.mean()))
    return found


def calculate(trades, fixed, asof, settlements=None):
    """Full-history replay. UI date filters must be applied only to the returned frames."""
    asof=pd.Timestamp(asof).normalize();trades=[t for t in ordered(trades) if pd.Timestamp(t['date'])<=asof]
    if not trades:return pd.DataFrame(),pd.DataFrame(),pd.DataFrame()
    official=settlement_values(trades,fixed,asof) if settlements is None else settlements
    contracts={t['contract']:t for t in trades}
    rows=fixed[fixed.kind.eq('BFA') & fixed.identity.isin(contracts) & fixed.quote_day.le(asof)].copy()
    marks={}
    for k,part in rows.groupby('identity'):
        t=contracts[k];part=part[part.vessel.eq(t['vessel']) & part['product'].eq(t['product']) &
                               part.currency.eq(t['currency']) & part.unit.eq(t['unit']) & part.quote_day.le(pd.Timestamp(t['delivery_end']))]
        marks[k]=part.sort_values('quote_day').drop_duplicates('quote_day',keep='last').set_index('quote_day').price.dropna()
    first=pd.Timestamp(trades[0]['date'])
    dates=sorted(set(rows.loc[rows.quote_day.ge(first),'quote_day']) | {pd.Timestamp(t['date']) for t in trades} |
                 {pd.Timestamp(v[0]) for v in official.values() if first<=pd.Timestamp(v[0])<=asof} | {asof})
    state={k:{'net':0.0,'cost':0.0,'realized':0.0,'settled':False} for k in contracts}
    history=[];details=[];cursor=0;previous=0.0;activated=set()
    for date in dates:
        while cursor<len(trades) and pd.Timestamp(trades[cursor]['date'])<=date:
            t=trades[cursor];s=state[t['contract']]
            if s['settled']:raise ValueError('A transaction occurs after verified final settlement.')
            apply_trade(s,t);activated.add(t['contract']);cursor+=1
        dayrows=[]
        for k,s in state.items():
            t=contracts[k];settle=official.get(k)
            if settle and pd.Timestamp(settle[0])<=date and not s['settled']:
                s['realized']+=s['net']*(float(settle[1])-s['cost']);s.update(net=0.0,cost=0.0,settled=True)
            quote=marks.get(k,pd.Series(dtype=float)).loc[:date]
            mark=float(settle[1]) if s['settled'] else float(quote.iloc[-1]) if len(quote) else np.nan
            markdate=pd.Timestamp(settle[0]) if s['settled'] else quote.index[-1] if len(quote) else pd.NaT
            expired=date>pd.Timestamp(t['delivery_end']) and abs(s['net'])>EPS
            # An expired outstanding contract cannot be valued using its last traded quote as settlement.
            unreal=0.0 if abs(s['net'])<EPS else np.nan if expired or pd.isna(mark) else s['net']*(mark-s['cost'])
            status='Settled' if s['settled'] else 'Closed' if abs(s['net'])<EPS else 'Settlement pending' if expired else 'Missing quote' if pd.isna(mark) else 'Prior quote' if pd.notna(markdate) and markdate<date else 'Quoted'
            if k in activated:
                dayrows.append({'Date':date,'Contract ID':k,'Vessel':t['vessel'],'Benchmark':t['product'],
                                'Contract':t.get('label',k),'Net Position':s['net'],'Average Cost':s['cost'] if abs(s['net'])>EPS else np.nan,
                                'BFA':mark,'Quote Date':markdate,'Realized P&L':s['realized'],'Unrealized P&L':unreal,
                                'Total P&L':s['realized']+unreal,'Status':status})
        table=pd.DataFrame(dayrows)
        total=table['Total P&L'].sum(min_count=len(table)) if len(table) else 0.0
        daily=total-previous if pd.notna(total) and pd.notna(previous) else np.nan
        history.append({'Date':date,'Realized P&L':table['Realized P&L'].sum(),'Unrealized P&L':table['Unrealized P&L'].sum(min_count=len(table)),
                        'Total P&L':total,'Daily P&L':daily,'Gross Days':table['Net Position'].abs().sum(),
                        'Net Position':table['Net Position'].sum(),'Valuation Complete':bool(table['Total P&L'].notna().all())})
        details.extend(dayrows);previous=total
    detail=pd.DataFrame(details);return detail[detail.Date.eq(asof)].copy(),pd.DataFrame(history),detail


def risk_state(positions, limit):
    if not math.isfinite(float(limit)) or float(limit)<=0:raise ValueError('Loss limit must be positive.')
    if positions.empty:total=0.0
    else:total=positions['Total P&L'].sum(min_count=len(positions))
    if pd.isna(total):return 'Unknown',None
    loss=max(0.0,-float(total));return ('Red' if loss>=limit else 'Yellow' if loss>=.8*limit else 'Normal'),float(total)


def permits(before, candidate, fixed, asof, limit):
    """Whole-account restriction; compare contract directions, never net different contracts."""
    old,_,_=calculate(before,fixed,asof);new,_,_=calculate(candidate,fixed,asof)
    status,_=risk_state(old,limit)
    was_restricted=status in ('Red','Unknown')
    if status not in ('Red','Unknown'):
        # Unknown valuation introduced by a new transaction cannot evade the limit either.
        status,_=risk_state(new,limit)
        if status not in ('Red','Unknown'):return True,''
    a=old.set_index('Contract ID')['Net Position'].to_dict() if len(old) else {}
    b=new.set_index('Contract ID')['Net Position'].to_dict() if len(new) else {}
    # A batch must not open risk and close it again to disguise the intermediate
    # exposure. Changed historical records are checked by the net ledger edit,
    # while genuinely new fills are checked one by one in execution order.
    original={t['id']:t for t in before}
    simulated=dict(a)
    for t in ordered(candidate) if was_restricted else []:
        if t['id'] in original:continue
        k=t['contract'];x=simulated.get(k,0.0)
        y=x+float(t['days'])*(1 if t['side']=='Buy' else -1)
        if abs(y)>abs(x)+EPS or (abs(y)>EPS and x*y<0):
            return False,'Account loss limit reached or valuation incomplete. The batch contains a new or reversed exposure.'
        simulated[k]=y
    for k in set(a)|set(b):
        x,y=a.get(k,0.0),b.get(k,0.0)
        if abs(y)>abs(x)+EPS or (abs(y)>EPS and x*y<0):
            return False,'Account loss limit reached or valuation incomplete. Only reductions and closes are allowed; reversals are blocked.'
    return True,''


def replace_trade(book, trade):
    value=deepcopy(book);validate_trade(trade)
    index=next((i for i,t in enumerate(value['trades']) if t['id']==trade['id']),None)
    if index is None:value['trades'].append(trade)
    else:value['trades'][index]=trade
    ordered(value['trades']);return value
