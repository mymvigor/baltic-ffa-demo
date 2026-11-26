"""Common-sample Pearson analysis. Seasonal resemblance is not prediction."""
from hashlib import blake2s
from itertools import combinations
import numpy as np
import pandas as pd
import streamlit as st
from .analytics import options,indicators,change_volatility,unit_label
from .series import inventory,preferred_series,source_frame,calendar_index,basis_frame,calendar_axis,reference_year,ordered_series
from .preferences import choice,multi,toggle
from .predictive import MIN_RELATION
from .ui import chart,date_axis,display,COLORS

FEATURES=('Basis','Basis %','Basis Change','MA5','MA20','MA5 − MA20','Momentum 5D','Volatility 20D')


def base_leg(key):
    return key[0] if len(key)==2 and isinstance(key[0],tuple) else key


def remembered(key):
    short=blake2s(key.encode(),digest_size=6).hexdigest()
    return st.session_state.get('_ffa_preferences',{}).get(short,[])


def freeze(value):
    return tuple(map(freeze,value)) if isinstance(value,list) else value


def catalog(candidates,labels,remembered_keys=()):
    choices={leg:labels[leg] for leg in candidates}
    for leg in candidates:
        for name in FEATURES:
            if name.startswith('Basis') and leg[2]!='BFA':continue
            label=labels[leg].rsplit(' · ',1)[0]+' · % of Spot' if name=='Basis %' else labels[leg]
            choices[(leg,name)]=f"{name} · {label}"
    # Offer spreads for the chosen legs (plus rolling neighbours), not every
    # quadratic combination. Referenced pairs survive removing their raw lines.
    pool=set()
    for raw in remembered_keys:
        k=freeze(raw)
        if len(k)==3 and k[0]=='Spread':
            if k[1] not in candidates or k[2] not in candidates:continue
            pool.update((k[1],k[2]));choices[k]=f"Spread · {labels[k[1]]} − {labels[k[2]]}"
        else:
            leg=base_leg(k)
            if leg in candidates:pool.add(leg)
    pool.update(leg for leg in candidates if len(leg)==8 and leg[6]=='Rolling' and leg[7] in ('+1MON','+2MON'))
    for a,b in combinations(sorted(pool,key=str),2):
        if len(a)!=8 or len(b)!=8:continue
        if a[:2]==b[:2] and a[2]==b[2]=='BFA' and a[3:6]==b[3:6]:
            if candidates[a][1].delivery_start.max()>candidates[b][1].delivery_start.max():a,b=b,a
            choices[('Spread',a,b)]=f"Spread · {labels[a]} − {labels[b]}"
    return choices


def _raw(leg,candidates,fixed,bounds):
    raw_leg=leg[:8] if len(leg)==10 and leg[-2]=='year' else leg
    raw=candidates[raw_leg][1]
    scope=fixed[fixed.vessel.eq(leg[0])&fixed['product'].eq(leg[1])&fixed.kind.eq(leg[2])]
    return source_frame(raw_leg,candidates,sorted(scope.quote_day.unique()),bounds)


def resolve(key,candidates,fixed,bounds):
    if len(key)==3 and key[0]=='Spread':
        a,b=key[1:];x=_raw(a,candidates,fixed,bounds);y=_raw(b,candidates,fixed,bounds).reindex(x.index)
        out=x.copy();out['price']=x.price-y.price
        out['segment']=(x.segment.ne(x.segment.shift())|y.segment.ne(y.segment.shift())).cumsum()
        roll=out.roll_from.isna()&y.roll_from.notna()
        out.loc[roll,['roll_from','roll_to']]=y.loc[roll,['roll_from','roll_to']]
        return out,a,'Near-leg price − far-leg price'
    feature=key[1] if len(key)==2 and isinstance(key[0],tuple) else None
    leg=base_leg(key);data=_raw(leg,candidates,fixed,bounds)
    formula='Stored unadjusted price'
    if feature and feature.startswith('Basis'):
        spot=next((k for k in candidates if len(k)==8 and k[:2]==leg[:2] and k[2]=='Spot' and k[3:5]==leg[3:5]),None)
        if spot is None:raise ValueError('Matching same-benchmark, same-unit Spot is unavailable.')
        data=basis_frame(data,_raw(spot,candidates,fixed,bounds),feature=='Basis %')
        formula='BFA − same-day Spot' if feature!='Basis %' else '100 × (BFA − Spot) / positive Spot'
        if feature=='Basis Change':
            data['price']=data.groupby('segment').price.diff();formula='Within-contract one-session change in (BFA − Spot)'
    elif feature in ('MA5','MA20','MA5 − MA20'):
        stats=indicators(data)
        data['price']=stats.ma5 if feature=='MA5' else stats.ma20 if feature=='MA20' else stats.ma5-stats.ma20
        formula='Mean of 5 valid same-contract quotes' if feature=='MA5' else 'Mean of 20 valid same-contract quotes' if feature=='MA20' else 'MA5 − MA20, within the same contract'
    elif feature=='Momentum 5D':
        raw=data.copy()
        data['price']=raw.price-raw.price.shift(5)
        valid=pd.concat([raw.segment.eq(raw.segment.shift(i))&raw.price.shift(i).notna() for i in range(6)],axis=1).all(axis=1)
        data['price']=data.price.where(valid);formula='Price(t) − Price(t−5), all quotes present in one contract'
    elif feature=='Volatility 20D':
        data['price']=change_volatility(data,20);formula='Sample SD of 20 within-contract absolute daily changes; not annualized'
    return data,leg,formula


def frequency_frame(data,frequency):
    if frequency=='Daily':return data.copy()
    rule='W-SUN' if frequency=='Weekly' else 'M'
    records=[]
    for period,group in data.groupby(data.index.to_period(rule)):
        valid=group[group.price.notna()]
        mixed=valid.segment.nunique()>1
        row=group.iloc[-1].copy()
        row['price']=valid.price.mean() if len(valid) and not mixed else np.nan
        row['segment']=valid.segment.iloc[-1] if len(valid) and not mixed else np.nan
        row['observed_day']=(valid.observed_day.iloc[-1] if 'observed_day' in valid else valid.index[-1]) if len(valid) else pd.NaT
        row.name=period.start_time;records.append(row)
    if not records:return data.iloc[:0].copy()
    out=pd.DataFrame(records)
    periods=pd.period_range(data.index.min(),data.index.max(),freq=rule).to_timestamp()
    return out.reindex(periods)


def transform(data,kind):
    result=data.copy()
    if kind=='Price / Level':return result
    previous=data.price.shift()
    valid=data.segment.eq(data.segment.shift())
    result['price']=(data.price-previous).where(valid)
    if kind=='Change (%)':result['price']=100*result.price/previous.where(previous.gt(1e-8))
    return result


def align_context(selected,candidates,fixed,alignment,asof):
    fixed_legs=[base_leg(k) if not(len(k)==3 and k[0]=='Spread') else k[1] for k in selected]
    contracts=[k for k in fixed_legs if k[2]=='BFA' and k[6]=='Fixed']
    if alignment=='Calendar Overlay' and contracts:
        seasons={(k[5],candidates[k][1].delivery_start.iloc[0].month,candidates[k][1].delivery_end.iloc[0].month) for k in contracts}
        if len(seasons)>1:raise ValueError('Calendar overlay needs matching delivery seasons and tenors. Use Actual Date for different maturities.')
    return reference_year(contracts,candidates,asof)


def clip_native(data,leg,candidates,alignment,asof,reference_year,year_offset):
    out=data.loc[:asof].copy()
    if len(leg)==10 and leg[-2]=='year':out=out[out.index.year==int(leg[-1])]
    elif alignment=='Calendar Overlay' and (leg[2]=='Spot' or leg[6]=='Rolling'):
        year=candidates[leg][1].quote_day.max().year
        out=out[out.index.year==year]
    if alignment=='Actual Date':return out,None
    if leg[2]=='BFA' and leg[6]=='Fixed':anchor=candidates[leg][1].delivery_start.iloc[0].year
    else:
        year=int(leg[-1]) if len(leg)==10 else candidates[leg][1].quote_day.max().year
        anchor=year+year_offset
    cutoff=calendar_index([asof],reference_year if reference_year else asof.year)[0]
    return out.loc[calendar_index(out.index,anchor)<=cutoff],anchor


def mapped(data,anchor):
    result=data.copy()
    if anchor is not None:result.index=calendar_index(result.index,anchor)
    return result


def matrix_stats(values,minimum=MIN_RELATION):
    common=values.replace([np.inf,-np.inf],np.nan).dropna()
    corr=common.corr(method='pearson') if len(common)>=minimum else pd.DataFrame(np.nan,index=values.columns,columns=values.columns)
    return corr,len(common)


def matrix_analysis(values,minimum=MIN_RELATION):
    """Prefer common samples; never erase valid pairs because six years'
    weekdays have no all-series intersection. Pair-specific N forbids ranking.
    """
    clean=values.replace([np.inf,-np.inf],np.nan)
    corr,n=matrix_stats(clean,minimum)
    if n>=minimum:return corr,pd.DataFrame(n,index=values.columns,columns=values.columns),True
    valid=clean.notna().astype(int)
    return clean.corr(min_periods=minimum),valid.T.dot(valid),False


def rolling_stats(a,b,window):
    values=pd.concat([a.price,b.price],axis=1);values.columns=['x','y']
    count=values.notna().all(axis=1).rolling(window,min_periods=1).sum()
    paired=values.where(values.notna().all(axis=1),np.nan)
    corr=paired.x.rolling(window,min_periods=min(MIN_RELATION,window)).corr(paired.y).clip(-1,1)
    corr=corr.where(values.notna().all(axis=1))
    return corr,count


def future_change(target,h,percent=False):
    future=target.price.shift(-h)
    complete=pd.concat([target.price.shift(-i).notna()&target.segment.eq(target.segment.shift(-i)) for i in range(h+1)],axis=1).all(axis=1)
    value=(future-target.price).where(complete)
    return 100*value/target.price.where(target.price.gt(1e-8)) if percent else value


def lead_lag_stats(signal,target,percent=False,minimum=MIN_RELATION):
    # Target calendar is kept intact BEFORE taking h-session changes. Never
    # compress a missing quote into an adjacent observation.
    labels={h:future_change(target,h,percent) for h in (1,5,10,20)}
    return aligned_lead_lag_stats(signal.price,labels,minimum)


def aligned_lead_lag_stats(signal,labels,minimum=MIN_RELATION):
    joined=pd.concat([signal.rename('signal'),* [v.rename(str(h)) for h,v in labels.items()]],axis=1).replace([np.inf,-np.inf],np.nan)
    common=joined.dropna();shared=len(common)>=minimum
    rows=[]
    for h in labels:
        pair=common if shared else joined[['signal',str(h)]].dropna()
        r=pair.signal.corr(pair[str(h)]) if len(pair)>=minimum and pair.signal.nunique()>1 and pair[str(h)].nunique()>1 else np.nan
        rows.append(dict(horizon=h,corr=r,n=len(pair),shared=shared))
    return pd.DataFrame(rows)


def correlation_view(load,first,last,key):
    alignment=choice('Alignment',['Actual Date','Calendar Overlay'],key+'_align',control='segmented_control')
    data_type=choice('Data Type',['Absolute Change','Change (%)','Price / Level'],key+'_type',control='segmented_control')
    view=choice('View',['Correlation Matrix','Rolling Correlation','Lead/Lag Correlation'],key+'_view',control='segmented_control')
    frequency=choice('Frequency',['Daily','Weekly','Monthly'],key+'_frequency',control='segmented_control') if view!='Lead/Lag Correlation' else 'Daily'
    with st.popover('Series scope'):
        wide=toggle('Include other benchmarks',key+'_wide')
    with st.spinner('Loading price series…'):
        market=load(first,last,wide=wide)
    universe=options(market.fixed,market.rolling) if wide else market.local
    bounds=(pd.Timestamp(market.fixed.quote_day.min()),pd.Timestamp(last))
    candidates,labels=inventory(universe,bounds,monthly=False)
    if not candidates:st.info('No eligible price series.');return
    candidates={k:candidates[k] for k in ordered_series(candidates,labels,last)}
    selection_key=key+'_series';choices=catalog(candidates,labels,remembered(selection_key))
    defaults=preferred_series(candidates)
    selected=multi('Series',choices,selection_key,defaults,len(choices),format_func=choices.get)
    if len(selected)<2:st.info('Select at least two series. Add two BFA legs to make their spread available in Series.');return
    needed=selected
    if view=='Lead/Lag Correlation':
        raw_targets=[k for k in selected if k in candidates]
        if not raw_targets:
            st.info('Add a Spot, Fixed or Rolling price series as the future-price target. Derived factors can be signals.');return
        signals=[k for k in selected if any(t!=k for t in raw_targets)]
        default_signal=next((k for k in signals if k not in candidates),signals[0])
        signal=choice('Signal',signals,key+'_signal',default_signal,format_func=choices.get) if len(signals)>1 else signals[0]
        targets=[k for k in raw_targets if k!=signal]
        target=choice('Target',targets,key+'_target',targets[0],format_func=choices.get) if len(targets)>1 else targets[0]
        st.caption(f'Signal: {choices[signal]} → future price target: {choices[target]}')
        needed=[signal,target]
    asof=min(pd.Timestamp(last),market.fixed.quote_day.max())
    try:
        ref,offset=align_context(needed,candidates,market.fixed,alignment,asof)
        native={};frames={};anchors={};formulas=[]
        for k in needed:
            data,leg,formula=resolve(k,candidates,market.fixed,bounds)
            data,anchor=clip_native(data,leg,candidates,alignment,asof,ref,offset)
            native[k]=data;anchors[k]=anchor
            # Aggregate on a shared seasonal grid; native Monday dates differ
            # across years. Keep actual quote dates as separate metadata.
            frames[k]=transform(frequency_frame(mapped(data,anchor),frequency),data_type)
            shown=formula if data_type=='Price / Level' else f'Within-contract Δ of ({formula})' if data_type=='Absolute Change' else f'100 × Δ({formula}) / positive prior ({formula})'
            formulas.append(choices[k]+': '+shown)
    except (ValueError,KeyError) as error:st.info(str(error));return
    if data_type=='Price / Level':st.caption('Level correlation can reflect shared trends and contract repricing. It is not evidence of a leading signal.')
    elif data_type=='Change (%)':st.caption('Percentage changes require a positive prior level. Missing quotes and roll jumps are excluded.')
    if alignment=='Calendar Overlay':st.caption('Seasonal analogy only. Matching delivery seasons, actual source dates, and the current seasonal cutoff are preserved; later seasonal observations are excluded.')
    if view=='Correlation Matrix':
        values=pd.concat([frames[k].price.rename(choices[k]) for k in selected],axis=1)
        corr,counts,shared=matrix_analysis(values)
        if not corr.notna().any().any():st.info(f'No eligible pair. Need {MIN_RELATION} common observations per pair with variation; try Calendar Overlay for different years.');return
        fig=chart('Series',max(390,25*len(selected)+150))
        fig.add_heatmap(x=list(corr.columns),y=list(corr.index),z=corr.to_numpy(),zmin=-1,zmax=1,zmid=0,
                        colorscale='RdBu',reversescale=True,text=corr.round(2).to_numpy(),texttemplate='%{text}',customdata=counts.to_numpy(),
                        hovertemplate='%{x}<br>%{y}<br>Pearson r %{z:.3f}<br>Valid N %{customdata}<extra></extra>')
        display(fig,key+'_matrix')
        pairs=[(abs(corr.iloc[i,j]),corr.iloc[i,j],i,j) for i in range(len(selected)) for j in range(i) if np.isfinite(corr.iloc[i,j])]
        if pairs and shared:
            _,r,i,j=max(pairs)
            n=int(counts.iloc[i,j])
            st.caption(f"Strongest observed relationship: {choices[selected[i]]} / {choices[selected[j]]} · r {r:+.2f}, common N {n}. {'Strong' if abs(r)>=.7 else 'Moderate' if abs(r)>=.4 else 'Weak'} descriptive association; no predictive edge established.")
        elif pairs:
            strong=[(r,i,j) for _,r,i,j in pairs if abs(r)>=.7]
            text='; '.join(f'{choices[selected[i]]} / {choices[selected[j]]}: r {r:+.2f}, N {int(counts.iloc[i,j])}' for r,i,j in strong[:3])
            st.caption(('Strong observed pairs (not ranked): '+text+'. ' if strong else 'No strong pairwise association. ')+ 'All-series overlap is insufficient; cells use pairwise common dates. Different samples are not ranked and do not establish prediction.')
        else:st.caption('No defined cross-series coefficient: check variation and overlapping observations.')
    elif view=='Rolling Correlation':
        base=choice('Reference series',selected,key+'_reference',selected[0],format_func=choices.get)
        window=choice('Rolling window',[20,60,120],key+'_rolling_window',60,format_func=lambda n:f'{n} observations')
        fig=chart('Pearson r',430);summary=[]
        for i,k in enumerate(selected):
            if k==base:continue
            corr,n=rolling_stats(frames[base],frames[k],window)
            if corr.notna().any():
                corr=corr.loc[corr.first_valid_index():corr.last_valid_index()]
                n=n.reindex(corr.index)
                dates_a=frames[base].observed_day.reindex(corr.index).dt.strftime('%d %b %Y').fillna('No quote')
                dates_b=frames[k].observed_day.reindex(corr.index).dt.strftime('%d %b %Y').fillna('No quote')
                fig.add_scatter(x=corr.index,y=corr,name=choices[k],mode='lines',connectgaps=False,customdata=n,
                                hovertemplate='%{x|%d %b}<br>r %{y:.3f}<br>Valid N %{customdata[0]}<br>Reference date %{customdata[1]}<br>Series date %{customdata[2]}<extra>%{fullData.name}</extra>')
                fig.data[-1].customdata=list(zip(n,dates_a,dates_b))
                recent=corr.dropna().tail(window)
                stability='too few estimates to assess stability' if len(recent)<10 else 'variable' if recent.max()-recent.min()>.5 else 'relatively stable in this window'
                summary.append(f"{choices[k]}: latest r {recent.iloc[-1]:+.2f}; {stability}")
        if not fig.data:st.info('Insufficient paired observations or variation for the selected rolling window.');return
        fig.update_yaxes(range=[-1,1]);xs=[pd.Timestamp(x) for trace in fig.data for x in trace.x]
        (calendar_axis if alignment=='Calendar Overlay' else date_axis)(fig,(min(xs),max(xs)));display(fig,key+'_rolling')
        st.caption(' · '.join(summary)+'. Pairwise windows; valid N is shown on hover. Descriptive stability is not a forecast.')
        st.caption(f'{window}-period window, at least {min(MIN_RELATION,window)} paired observations. Missing rows are excluded.')
    else:
        # Calculate on each native daily target calendar, then align the resulting
        # h-session labels to the signal. No synthetic calendar/session skipping.
        source=mapped(transform(native[signal],data_type),anchors[signal])
        raw_target=native[target]
        # lead_lag_stats receives precomputed native labels via explicit alignment.
        labels_future={h:mapped(raw_target.assign(price=future_change(raw_target,h,data_type=='Change (%)')),anchors[target]).price for h in (1,5,10,20)}
        statistics=aligned_lead_lag_stats(source.price,labels_future)
        shared=bool(statistics.shared.iloc[0]);rows=[(int(row.horizon),float(row['corr'])) for _,row in statistics.iterrows()]
        numbers=[int(n) for n in statistics.n]
        valid=[(h,r) for h,r in rows if np.isfinite(r)]
        if not valid:st.info(f"No eligible horizon: {', '.join(f'{h}D N {n}' for (h,r),n in zip(rows,numbers))}. Need {MIN_RELATION} valid cases with variation.");return
        fig=chart('Pearson r',380)
        fig.add_scatter(x=[f'{h}D' for h,r in rows],y=[r for h,r in rows],mode='lines+markers',customdata=numbers,
                        hovertemplate='%{x} ahead<br>r %{y:.3f}<br>Common N %{customdata}<extra></extra>')
        fig.update_yaxes(range=[-1,1]);display(fig,key+'_lead_lag')
        h,r=max(valid,key=lambda pair:abs(pair[1])) if shared else next(((h,r) for h,r in valid if abs(r)>=.4),valid[0])
        n=numbers[[h for h,r in rows].index(h)]
        st.caption(f"Signal at t ({data_type}) versus target change from t to t+h on its daily assessment calendar. {'Strongest common-sample association' if shared else 'Observed association (unequal samples; no ranking)'}: {h}D, r {r:+.2f}, N {n}. Higher signal is associated with {'higher' if r>0 else 'lower'} future target changes. {'Potential candidate for OOS testing' if abs(r)>=.4 else 'Weak association'}; this does not establish prediction.")
        missing=[f'{h}D N {n}' for (h,r),n in zip(rows,numbers) if not np.isfinite(r)]
        if missing:st.caption('Unavailable horizons: '+', '.join(missing)+f'; need {MIN_RELATION} valid cases with variation.')
        st.caption('Daily forward labels overlap; N counts observations, not independent trials. Historical publication times and revisions are unverified.')
    with st.popover('Definitions'):
        for formula in formulas:st.caption(formula)
        st.caption('Changes are within-contract differences; % changes = 100 × change / positive previous level. Weekly/monthly values are available daily means; periods spanning a roll are excluded. Pearson uses common valid samples, without filled quotes.')
        st.caption('Factors sharing price inputs can be mechanically correlated. A high coefficient alone does not establish incremental predictive information.')
        st.caption('Data Type also applies to factors. Choose Price / Level to use Basis Change, MA, momentum or volatility values directly.')
