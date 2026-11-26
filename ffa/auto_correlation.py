"""Four automatic, version-cached correlation heatmaps; no new forecast model."""
import re
import numpy as np
import pandas as pd
import streamlit as st
from .analytics import options,indicators,change_volatility,unit_label
from .correlation import resolve,transform,mapped,matrix_analysis,future_change
from .series import inventory,calendar_index,basis_frame
from .selection import contract_choice,contract_label
from .preferences import choice
from .settlement import sessions,SUPPORTED
from .predictive import MIN_RELATION,independent_forecasts
from .model import VESSELS
from .ui import chart,display

HORIZONS=(1,5,10,20)
COLORS=[[0,'#d9979c'],[.5,'#f6f8fa'],[1,'#83beb9']]


def scoped(fixed,vessel,product,asof=None):
    mask=fixed.vessel.eq(vessel)&fixed['product'].eq(product)&np.isfinite(fixed.price)
    if asof is not None:mask &= fixed.quote_day.le(asof)
    return fixed[mask].copy()


def quote_unit(rows):
    units=list(rows[['currency','unit']].drop_duplicates().itertuples(index=False,name=None))
    return ('USD','day') if ('USD','day') in units else units[0] if units else ('USD','day')


def compatible(rows,unit):
    return rows[rows.currency.eq(unit[0])&rows.unit.eq(unit[1])]


def universe(fixed,rolling):
    bounds=(fixed.quote_day.min(),fixed.quote_day.max())
    candidates,labels=inventory(options(fixed,rolling),bounds,monthly=False)
    return candidates,bounds


def raw_frame(key,candidates,fixed,bounds):
    if key is None:return empty_frame()
    data=resolve(key,candidates,fixed,bounds)[0]
    if key[2]=='BFA':
        caps={}
        for end in data.delivery_end.dropna().unique():
            end=pd.Timestamp(end);calendar=sessions(end.to_period('M'))
            caps[end]=min(end,calendar[-1]) if calendar is not None and key[1] in SUPPORTED and key[3:5]==('USD','day') else end
        valid=pd.Series(data.index,index=data.index).le(pd.to_datetime(data.delivery_end.map(caps)))
        data['price']=data.price.where(valid)
        data['observed_day']=data.observed_day.where(data.price.notna())
    return data


def empty_frame():
    return pd.DataFrame({'price':pd.Series(dtype=float),'segment':pd.Series(dtype=float),
                         'observed_day':pd.Series(dtype='datetime64[ns]')},index=pd.DatetimeIndex([]))


def spot_key(candidates):
    return next((k for k in candidates if len(k)==8 and k[2]=='Spot'),None)


def active_contracts(fixed,asof):
    rows=fixed[fixed.kind.eq('BFA')&fixed.delivery_start.notna()&fixed.delivery_end.ge(asof)&fixed.quote_day.le(asof)].sort_values('quote_day')
    return rows.drop_duplicates('identity',keep='last').sort_values(['delivery_start','tenor'])


def main_contract(fixed,asof):
    contracts=active_contracts(fixed,asof)
    if contracts.empty:return None
    monthly=contracts[contracts.tenor.eq('month')]
    pool=monthly if len(monthly) else contracts
    future=pool[pool.delivery_start.gt(asof)]
    if len(future):pool=future
    fresh=pool[pool.quote_day.ge(asof-pd.Timedelta(days=7))]
    return (fresh if len(fresh) else pool).iloc[0].identity


def fixed_key(candidates,identity):
    return next((k for k in candidates if len(k)==8 and k[6]=='Fixed' and k[7]==identity),None)


def forward_keys(candidates,fixed,asof):
    def rolling(code):
        keys=[k for k in candidates if len(k)==8 and k[6]=='Rolling' and re.fullmatch(re.escape(code)+r'(?:#\d+)?',k[7])]
        if not keys:return None
        return max(keys,key=lambda k:(candidates[k][1].price.notna().sum()>=MIN_RELATION,candidates[k][1].quote_day.max(),len(candidates[k][1])))
    near=rolling('+1MON');far=rolling('+3MON') or rolling('+2MON')
    contracts=active_contracts(fixed,asof)
    monthly=contracts[contracts.tenor.eq('month')&contracts.delivery_start.gt(asof)]
    if near is None and len(monthly):near=fixed_key(candidates,monthly.iloc[0].identity)
    if far is None and len(monthly)>1:far=fixed_key(candidates,monthly.iloc[min(2,len(monthly)-1)].identity)
    return near,far


def date_range(dates):
    values=pd.to_datetime(pd.Series(dates),errors='coerce').dropna()
    return f'{values.min():%d %b %Y} – {values.max():%d %b %Y}' if len(values) else 'No matched quotes'


def pair_matrix(frames,method,seasonal=False):
    names=list(frames)
    if not names:frames={'Unavailable':empty_frame()};names=list(frames)
    if seasonal and len(frames)>1:
        actual=[v.dropna(subset=['price']) for v in frames.values()]
        if all(len(v) for v in actual):
            start=max(v.index.min() for v in actual);end=min(v.index.max() for v in actual)
            frames={name:value.loc[start:end] for name,value in frames.items()}
    values=pd.concat([frames[name].price.rename(name) for name in names],axis=1)
    corr,counts,shared=matrix_analysis(values)
    common=values.replace([np.inf,-np.inf],np.nan).dropna()
    details=np.empty((len(names),len(names),5),dtype=object)
    for i,a in enumerate(names):
        for j,b in enumerate(names):
            index=common.index if shared else values[[a,b]].replace([np.inf,-np.inf],np.nan).dropna().index
            reason='' if np.isfinite(corr.iloc[i,j]) else f'Need {MIN_RELATION} valid pairs with variation'
            if len(names)==1:
                corr.iloc[i,j]=np.nan;reason='No valid historical counterpart'
            details[i,j]=[int(counts.iloc[i,j]),date_range(frames[a].observed_day.reindex(index)),date_range(frames[b].observed_day.reindex(index)),(method+(' · common sample' if shared else ' · pairwise valid dates')).replace(' · ','<br>'),reason]
    return dict(corr=corr,counts=counts,details=details,shared=shared)


def _history_frames(data,anchor,cutoff,mode):
    prepared=transform(data,mode)
    shifted=calendar_index(prepared.index,anchor)
    return mapped(prepared.loc[shifted<=cutoff],anchor)


def usable_history(current,past):
    pair=pd.concat([current.price,past.price],axis=1).replace([np.inf,-np.inf],np.nan).dropna()
    return len(pair)>=MIN_RELATION and pair.iloc[:,0].nunique()>1 and pair.iloc[:,1].nunique()>1


@st.cache_data(max_entries=12,show_spinner=False)
def spot_history(version,vessel,product,asof_text,mode,_fixed):
    asof=pd.Timestamp(asof_text);scope=scoped(_fixed,vessel,product,asof)
    unit=quote_unit(scope[scope.kind.eq('Spot')]);scope=compatible(scope,unit)
    if scope.empty:
        result=pair_matrix({},mode);result.update(current=str(asof.year),reason=f'No compatible Spot history for {vessel} {product}.');return result
    candidates,bounds=universe(scope,scope.iloc[:0]);key=spot_key(candidates)
    raw=raw_frame(key,candidates,scope,bounds).loc[:asof]
    current=asof.year;cutoff=calendar_index([asof],current)[0]
    annual=raw[raw.index.year==current]
    current_frame=_history_frames(annual,current,cutoff,mode)
    frames={};past=[]
    for year in sorted(raw.index.year.unique(),reverse=True):
        if year>current:continue
        annual=raw[raw.index.year==year]
        frame=_history_frames(annual,year,cutoff,mode)
        # A valid year supplies actual observations, never extrapolated prices.
        if year<current and usable_history(current_frame,frame):past.append((str(year),frame))
    for name,frame in past[:5]:frames[name]=frame
    frames={str(current):current_frame,**frames}
    result=pair_matrix(frames,f'Pearson · {vessel} {product} {unit_label(*unit)} · {mode} · calendar month/day through {asof:%d %b}',True)
    result.update(current=str(current),source=f'{vessel} {product} · {unit_label(*unit)}',coverage=date_range(raw.index),years=list(frames))
    if annual.empty:result['reason']=f'No Spot quotes in {current}; indices are not spliced to create history.'
    return result


@st.cache_data(max_entries=12,show_spinner=False)
def bfa_history(version,vessel,product,asof_text,mode,_fixed):
    asof=pd.Timestamp(asof_text);scope=scoped(_fixed,vessel,product,asof)
    scope=compatible(scope,quote_unit(scope[scope.kind.eq('BFA')]))
    identity=main_contract(scope,asof)
    if identity is None:
        result=pair_matrix({},mode);result.update(current='Unavailable',reason='No active fixed BFA contract.',source=f'{vessel} {product}');return result
    candidates,bounds=universe(scope,scope.iloc[:0]);key=fixed_key(candidates,identity)
    row=candidates[key][1].iloc[-1];anchor=row.delivery_start.year
    cutoff=calendar_index([asof],anchor)[0]
    current_frame=_history_frames(raw_frame(key,candidates,scope,bounds).loc[:asof],anchor,cutoff,mode)
    family=[]
    for k,(_,rows) in candidates.items():
        if len(k)!=8 or k[2]!='BFA' or k[6]!='Fixed':continue
        r=rows.iloc[0]
        if r.tenor!=row.tenor or r.delivery_start.month!=row.delivery_start.month or r.delivery_end.month!=row.delivery_end.month:continue
        if r.delivery_start.year>=anchor:continue
        lag=anchor-r.delivery_start.year
        expected=row.delivery_end-pd.DateOffset(years=lag)
        if row.delivery_end.is_month_end:expected+=pd.offsets.MonthEnd(0)
        if r.delivery_start!=row.delivery_start-pd.DateOffset(years=lag) or r.delivery_end!=expected:continue
        data=_history_frames(raw_frame(k,candidates,scope,bounds).loc[:asof],r.delivery_start.year,cutoff,mode)
        if usable_history(current_frame,data):family.append((r.delivery_start.year,contract_label(r).split(' · ')[0],data))
    family=sorted(family,key=lambda v:v[0],reverse=True)[:5]
    name=contract_label(row).split(' · ')[0]
    frames={name:current_frame}
    frames.update({label:data for year,label,data in family})
    result=pair_matrix(frames,f'Pearson · {vessel} {product} {unit_label(row.currency,row.unit)} · {mode} · same tenor, delivery season and year-shifted delivery distance',True)
    result.update(current=name,contract=identity,source=f'{vessel} {product} · {unit_label(row.currency,row.unit)}',coverage=date_range(scope[scope.kind.eq('BFA')].quote_day))
    return result


@st.cache_data(max_entries=8,show_spinner=False)
def market_history(version,vessel,product,asof_text,preferences,_fixed,_rolling):
    asof=pd.Timestamp(asof_text);frames={};definitions=[]
    calendar=sorted(_fixed.loc[_fixed.quote_day.le(asof),'quote_day'].unique())
    start=pd.Timestamp(calendar[-252]) if len(calendar)>=252 else pd.Timestamp(calendar[0]) if calendar else asof
    for ship in VESSELS:
        available=_fixed[_fixed.vessel.eq(ship)&_fixed.quote_day.le(asof)]
        products=list(available['product'].dropna().unique())
        preferred=dict(preferences).get(ship,())
        chosen=product if ship==vessel else next((p for p in preferred if p in products),products[0] if products else None)
        if chosen is None:continue
        scope=scoped(_fixed,ship,chosen,asof);unit=quote_unit(scope[scope.kind.eq('Spot')] if scope.kind.eq('Spot').any() else scope)
        scope=compatible(scope,unit)
        if scope.empty:continue
        rolling=compatible(_rolling[_rolling.vessel.eq(ship)&_rolling['product'].eq(chosen)&_rolling.quote_day.le(asof)],unit)
        candidates,bounds=universe(scope,rolling)
        near,far=forward_keys(candidates,scope,asof)
        for label,k in [('Spot',spot_key(candidates)),('Near BFA',near),('Far BFA',far)]:
            data=raw_frame(k,candidates,scope,bounds).loc[:asof]
            frame=transform(data,'Absolute Change').loc[start:asof]
            code=('unavailable' if k is None else 'Spot' if k[2]=='Spot' else candidates[k][0])
            frames[f'{ship} {label}']=frame
            definitions.append(f'{ship} {label}: {chosen} · {code} · {unit_label(*unit)}')
    result=pair_matrix(frames,'Pearson · absolute daily changes · last 252 assessment sessions · roll jumps excluded')
    for i in range(len(frames)):
        for j in range(len(frames)):
            result['details'][i,j,3]+='<br>'+definitions[i]+'<br>'+definitions[j]
    result.update(definitions=definitions,start=str(start.date()),asof=asof_text)
    return result


def _momentum(data):
    valid=pd.concat([data.price.shift(i).notna()&data.segment.eq(data.segment.shift(i)) for i in range(6)],axis=1).all(axis=1)
    return (data.price-data.price.shift(5)).where(valid)


def predictor_inputs(fixed,rolling,vessel,product,asof,kind,contract=None):
    scope=scoped(fixed,vessel,product,asof)
    relevant=scope[scope.kind.eq(kind)]
    if kind=='BFA' and contract:relevant=relevant[relevant.identity.eq(contract)]
    unit=quote_unit(relevant);scope=compatible(scope,unit)
    if scope.empty:return {},empty_frame(),{},'No compatible prices.'
    roll=compatible(rolling[rolling.vessel.eq(vessel)&rolling['product'].eq(product)&rolling.quote_day.le(asof)],unit)
    candidates,bounds=universe(scope,roll)
    near,far=forward_keys(candidates,scope,asof)
    chosen=fixed_key(candidates,contract) if kind=='BFA' else spot_key(candidates)
    target=raw_frame(chosen,candidates,scope,bounds).loc[:asof]
    spot=raw_frame(spot_key(candidates),candidates,scope,bounds).loc[:asof]
    forward_key=chosen if kind=='BFA' else near
    forward=raw_frame(forward_key,candidates,scope,bounds).loc[:asof]
    basis=basis_frame(forward,spot) if len(forward) else empty_frame()
    spread=empty_frame()
    if near is not None and far is not None:
        spread=resolve(('Spread',near,far),candidates,scope,bounds)[0].loc[:asof]
    stats=indicators(target) if len(target) else target
    factors={
        'Basis':basis.price,
        'Basis Change':basis.groupby('segment').price.diff(),
        'Spot Momentum (5D)':_momentum(spot),
        'BFA Momentum (5D)':_momentum(forward),
        f'{kind} MA5 − MA20':stats.ma5-stats.ma20 if len(stats) else pd.Series(dtype=float),
        'Calendar Spread':spread.price,
        f'{kind} Volatility (20D)':change_volatility(target,20) if len(target) else pd.Series(dtype=float),
    }
    forward_label=(candidates[forward_key][0] if forward_key is not None else 'unavailable')
    formulas={
        'Basis':f'{forward_label} BFA(t) − same-day compatible Spot(t)',
        'Basis Change':'Within-contract one-session change in Basis; roll jumps excluded',
        'Spot Momentum (5D)':'Spot(t) − Spot(t−5), five complete past Spot assessment sessions',
        'BFA Momentum (5D)':f'{forward_label} BFA(t) − BFA(t−5), all sessions in the same contract',
        f'{kind} MA5 − MA20':f'{kind} 5-quote mean − 20-quote mean, within the same contract',
        'Calendar Spread':f"Near BFA − far BFA; {candidates[near][0] if near else 'unavailable'} − {candidates[far][0] if far else 'unavailable'}",
        f'{kind} Volatility (20D)':'Sample SD of 20 past within-contract absolute changes; not annualized',
    }
    target_name=contract_label(candidates[chosen][1].iloc[-1]).split(' · ')[0] if kind=='BFA' and chosen is not None else 'BFA unavailable' if kind=='BFA' else 'Spot'
    return factors,target,formulas,f'{vessel} {product} {target_name} · {unit_label(*unit)}'


def nonoverlap(pair,ends):
    if pair.empty:return pair
    rows=pair.assign(date=pair.index,label_day=ends.reindex(pair.index),offset=0,contract='single target')
    return independent_forecasts(rows).loc[:,pair.columns]


def predictive_stats(factors,target,formulas,target_name):
    names=list(factors);columns=[f'{h}D' for h in HORIZONS]
    corr=pd.DataFrame(np.nan,index=names,columns=columns);counts=pd.DataFrame(0,index=names,columns=columns)
    details=np.empty((len(names),len(columns),5),dtype=object);candidates=[]
    values=pd.concat([value.rename(name) for name,value in factors.items()],axis=1).replace([np.inf,-np.inf],np.nan)
    for j,h in enumerate(HORIZONS):
        outcome=future_change(target,h)
        ends=pd.Series(target.index,index=target.index).shift(-h)
        all_values=pd.concat([values,outcome.rename('_outcome')],axis=1).replace([np.inf,-np.inf],np.nan)
        common=nonoverlap(all_values.dropna(),ends)
        shared=len(common)>=MIN_RELATION
        for i,name in enumerate(names):
            pair=common[[name,'_outcome']] if shared else nonoverlap(all_values[[name,'_outcome']].dropna(),ends)
            n=len(pair);counts.iloc[i,j]=n
            r=pair[name].corr(pair['_outcome']) if n>=MIN_RELATION and pair[name].nunique()>1 and pair['_outcome'].nunique()>1 else np.nan
            corr.iloc[i,j]=r
            reason='' if np.isfinite(r) else f'Need {MIN_RELATION} non-overlapping cases with variation'
            method=f'{target_name}<br>{formulas[name]}<br>Known at t versus target price(t+{h}) − price(t)<br>Non-overlapping labels; '+('common origins across factors' if shared else 'factor-specific origins; no ranking')
            details[i,j]=[n,date_range(pair.index),date_range(ends.reindex(pair.index)),method,reason]
            # A screening guard against a single chance regime; not a new score
            # or an independent OOS test. It never selects model parameters.
            if np.isfinite(r) and abs(r)>=.4 and n>=30:
                left,right=pair.iloc[:n//2],pair.iloc[n//2:]
                early=left[name].corr(left['_outcome']);late=right[name].corr(right['_outcome'])
                if np.isfinite(early) and np.isfinite(late) and np.sign(early)==np.sign(late)==np.sign(r):
                    candidates.append((name,h,float(r),n))
    return dict(corr=corr,counts=counts,details=details,candidates=candidates,source=target_name)


@st.cache_data(max_entries=12,show_spinner=False)
def predictive_history(version,vessel,product,asof_text,kind,contract,_fixed,_rolling):
    factors,target,formulas,name=predictor_inputs(_fixed,_rolling,vessel,product,pd.Timestamp(asof_text),kind,contract)
    if not factors:
        factors={'Unavailable':pd.Series(dtype=float)};formulas={'Unavailable':name}
    return predictive_stats(factors,target,formulas,name)


def heatmap(result,key,predictive=False):
    corr=result['corr'];text=[[f'{v:.2f}' if np.isfinite(v) else '' for v in row] for row in corr.to_numpy()]
    fig=chart('Factor' if predictive else 'Series',max(365,len(corr)*29+190))
    fig.update_layout(margin=dict(l=20,r=25,t=20,b=100),hoverlabel=dict(namelength=-1))
    fig.add_heatmap(x=list(corr.columns),y=list(corr.index),z=corr.to_numpy(),zmin=-1,zmax=1,zmid=0,
        colorscale=COLORS,colorbar=dict(title='Pearson r'),text=text,texttemplate='%{text}',textfont=dict(color='#19364a'),
        customdata=result['details'],hoverongaps=True,
        hovertemplate='%{y} / %{x}<br>r %{z:.3f} · valid N %{customdata[0]}<br>Row/signal quotes: %{customdata[1]}<br>Column/outcome quotes: %{customdata[2]}<br>%{customdata[3]}<br>%{customdata[4]}<extra></extra>')
    fig.update_xaxes(type='category',tickangle=-30)
    fig.update_yaxes(type='category',autorange='reversed')
    display(fig,key)


def historical_sentence(result):
    if result.get('reason'):return result['reason']
    name=result.get('current','Unavailable');corr=result['corr'];counts=result['counts']
    if name not in corr or len(corr)<2:return f"No usable historical counterpart for {name}; need {MIN_RELATION} paired quotes in the current seasonal interval."
    pairs=[(k,float(corr.loc[name,k]),int(counts.loc[name,k])) for k in corr.columns if k!=name and np.isfinite(corr.loc[name,k])]
    if not pairs:return f'Historical counterparts exist, but none has {MIN_RELATION} paired quotes with variation against {name}.'
    if result['shared']:
        label,r,n=max(pairs,key=lambda item:item[1])
        return f"Closest observed shape to {name}: {label} (r {r:+.2f}, N {n}); historical resemblance is not predictive ability."
    strong=[f'{label} (r {r:+.2f}, N {n})' for label,r,n in pairs if r>=.7]
    return ('Years/contracts worth review: '+', '.join(strong[:3]) if strong else f'No strong positive resemblance to {name} in available pairs')+'; pair samples differ, so no best-year ranking or prediction is inferred.'


def automatic_correlation_view(load,first,last,key,stamp,vessel,product,preferences):
    asof=pd.Timestamp(last)
    measure=choice('Historical Comparison',['Price Levels','Price Changes'],key+'_historical_measure',control='segmented_control')
    mode='Price / Level' if measure=='Price Levels' else 'Absolute Change'
    current=load(first,last,wide=False)
    version=(stamp,len(current.fixed),len(current.rolling),str(current.fixed.quote_day.min()),str(current.fixed.quote_day.max()))
    st.markdown('**Spot Historical Correlation**')
    with st.spinner('Updating historical comparisons…'):
        spot=spot_history(version,vessel,product,str(asof.date()),mode,current.fixed)
    heatmap(spot,key+'_auto_spot')
    st.caption(historical_sentence(spot))
    st.markdown('**BFA Historical Correlation**')
    bfa=bfa_history(version,vessel,product,str(asof.date()),mode,current.fixed)
    heatmap(bfa,key+'_auto_bfa')
    st.caption((f"Automatic contract {bfa.get('current','Unavailable')} · ")+historical_sentence(bfa))
    st.markdown('**Market Correlation**')
    # Only a recent cross-market window is needed here; do not fetch every old
    # benchmark/year merely to populate an unrestricted manual selector.
    with st.spinner('Updating cross-market relationships…'):
        wide=load((asof-pd.Timedelta(days=400)).date(),asof.date(),wide=True)
        market_version=(stamp,len(wide.fixed),len(wide.rolling),str(wide.fixed.quote_day.min()),str(wide.fixed.quote_day.max()))
        market=market_history(market_version,vessel,product,str(asof.date()),tuple((k,tuple(v)) for k,v in preferences.items()),wide.fixed,wide.rolling)
    heatmap(market,key+'_auto_market')
    pairs=[(market['corr'].iloc[i,j],i,j) for i in range(len(market['corr'])) for j in range(i) if np.isfinite(market['corr'].iloc[i,j])]
    strong=[(r,i,j) for r,i,j in pairs if abs(r)>=.7]
    names=market['corr'].index
    text='; '.join(f'{names[i]} / {names[j]} (r {r:+.2f}, N {int(market["counts"].iloc[i,j])})' for r,i,j in strong[:2])
    st.caption(('Strong change relationships worth review: '+text if strong else 'No strong price-change relationship in the available pairs')+'; descriptive links do not establish causality or a trading signal.')
    st.markdown('**Predictive Correlation**')
    kind=choice('Predictive Target',['Spot','BFA'],key+'_predictive_kind',control='segmented_control')
    contract=None
    if kind=='BFA':
        scope=scoped(current.fixed,vessel,product,asof);scope=compatible(scope,quote_unit(scope[scope.kind.eq('BFA')]))
        contracts=active_contracts(scope,asof)
        if len(contracts):contract=contract_choice('Predictive Contract',contracts,key+'_predictive_contract',main_contract(scope,asof))
    with st.spinner('Updating predictive-factor screening…'):
        predictive=predictive_history(version,vessel,product,str(asof.date()),kind,contract,current.fixed,current.rolling)
    heatmap(predictive,key+'_auto_predictive',True)
    items=predictive['candidates'][:2]
    text='; '.join(f'{name} → {h}D (r {r:+.2f}, N {n})' for name,h,r,n in items)
    st.caption(('No active fixed BFA target is available' if kind=='BFA' and contract is None else 'Candidates for OOS review: '+text if items else 'No factor passes the sample and chronological-direction screening')+'; no ranking, proven predictive edge or causal claim.')
    with st.popover('Calculation notes'):
        st.caption(f'Data through {asof:%d %b %Y}. Historical levels describe shape and can reflect shared trends, not prediction. Historical changes are absolute assessment-to-assessment changes.')
        st.caption('Years/contracts: current plus up to five usable historical counterparts, clipped to the current seasonal position and matched delivery distance. No cross-index splicing or filled quotes.')
        st.caption('Market: Spot and near/far BFA for four vessel classes, absolute daily changes over the last 252 assessment sessions. '+ '; '.join(market.get('definitions',[])))
        st.caption(f'At least {MIN_RELATION} valid paired observations are required. Common samples are preferred; otherwise each cell shows its own N and no cross-sample ranking is made.')
        st.caption('Predictive: factors at t versus subsequent 1/5/10/20-session absolute price changes. Native target paths must be complete and stay within the same contract; samples are non-overlapping for each horizon. Fixed BFA history may be too short for long horizons.')
        st.caption('Candidate captions require |r| ≥ 0.4 and consistent sign in two chronological halves (at least 15 cases each); this is a screening guard, not independent validation. Multiple comparisons and unverified historical publication/revision times can create chance relationships.')
