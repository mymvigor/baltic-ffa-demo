"""Shared searchable price inventory, native observations and seasonal coordinates."""
import pandas as pd
from .analytics import period_means,timeline,unit_label
from .selection import contract_label
from .preferences import multi


def inventory(series,bounds,kinds=None,monthly=True):
    kinds=set(['Spot','Fixed','Rolling','Spot monthly mean'] if kinds is None else kinds)
    candidates={leg:item for leg,item in series.items() if (leg[2] if leg[2]=='Spot' else leg[6]) in kinds and item[1].quote_day.between(*bounds).any()}
    labels={leg:(contract_label(item[1].iloc[0]) if leg[2]=='BFA' and leg[6]=='Fixed' else f"{leg[2] if leg[2]=='Spot' else 'Rolling'} · {item[0]}")+f" · {leg[0]} {leg[1]} · {unit_label(*leg[3:5])}" for leg,item in candidates.items()}
    for leg,item in list(candidates.items()):
        if leg[2]=='Spot' or leg[6]=='Rolling':
            for year,part in item[1].groupby(item[1].quote_day.dt.year):
                alias=(*leg,'year',str(year));candidates[alias]=(item[0],part)
                labels[alias]=f"{leg[2] if leg[2]=='Spot' else 'Rolling'}{year} · {item[0]} · {leg[0]} {leg[1]} · {unit_label(*leg[3:5])}"
    if monthly and 'Spot monthly mean' in kinds:
        for leg,item in series.items():
            if leg[2]=='Spot' and item[1].quote_day.between(*bounds).any():
                alias=(*leg,'monthly');candidates[alias]=item
                labels[alias]=f"Spot monthly mean · {leg[0]} {leg[1]} · {unit_label(*leg[3:5])}"
    return candidates,labels


def preferred_series(candidates):
    preferred=[leg for leg in candidates if leg[2]=='Spot' and len(leg)==8][:1]
    near=next((leg for leg in candidates if len(leg)==8 and leg[6]=='Rolling' and leg[-1]=='+1MON' and (not preferred or leg[:5]==(*preferred[0][:2],'BFA',*preferred[0][3:5]))),None)
    if near is not None:preferred.append(near)
    return preferred or list(candidates)[:2]


def ordered_series(candidates,labels,last):
    def priority(leg):
        row=candidates[leg][1].iloc[-1]
        if leg[2]=='Spot':return (0,len(leg)!=8,-int(leg[-1]) if len(leg)==10 else 0,labels[leg])
        if leg[6]=='Rolling':return (1,len(leg)!=8,0,labels[leg])
        return (2,row.delivery_end<pd.Timestamp(last),abs((row.delivery_start-pd.Timestamp(last)).days),labels[leg])
    return sorted(candidates,key=priority)


def select_series(candidates,labels,key,last,preferred=None):
    return multi('Series',ordered_series(candidates,labels,last),key,preferred or preferred_series(candidates),len(candidates),format_func=labels.get)


def source_frame(leg,candidates,days,bounds):
    raw=candidates[leg][1]
    data=timeline(raw,pd.DatetimeIndex(days)).loc[bounds[0]:bounds[1]]
    if len(leg)==9 and leg[-1]=='monthly':
        data=period_means(data,'price','M',bounds).set_index('date')
        data['kind']='Spot';data['roll_from']=data['roll_to']=None
    data=data.loc[raw.quote_day.min():raw.quote_day.max()].copy()
    data['observed_day']=pd.Series(data.index,index=data.index).where(data.price.notna())
    data['Observed']=data.index.strftime('%d %b %Y')
    return data


def calendar_index(index,anchor):
    index=pd.DatetimeIndex(index);mapped=index+pd.DateOffset(years=2000-int(anchor))
    leap=(index.month==2)&(index.day==29)&~mapped.is_leap_year
    return mapped+pd.to_timedelta(leap.astype(int)*12,unit='h')


def reference_year(contracts,candidates,asof):
    """Anchor expired analogue-only selections to the current seasonal position."""
    asof=pd.Timestamp(asof)
    if not contracts:return asof.year,0
    rows=[candidates[k][1].iloc[-1] for k in contracts]
    year=max(r.delivery_start.year for r in rows)
    if max(r.delivery_end for r in rows)<asof:
        year=asof.year+int(asof.month>max(r.delivery_end.month for r in rows))
    else:year=max(year,asof.year)
    return year,year-asof.year


def calendar_axis(fig,bounds):
    from .ui import date_axis
    date_axis(fig,bounds)
    lo,hi=map(pd.Timestamp,bounds)
    ticks=pd.date_range(lo.replace(day=1),hi+pd.offsets.MonthBegin(1),freq='MS')
    fig.update_xaxes(tickvals=ticks,ticktext=[f'{d:%b} · Y{d.year-2000:+d}' for d in ticks],
                     title='Calendar position · Y+0 is the reference year')


def basis_frame(forward,spot,percent=False):
    """BFA minus same-day same-benchmark Spot. No ffill or cross-index join."""
    result=forward.copy();aligned=spot.reindex(result.index)
    result['Spot']=aligned.price
    result['price']=forward.price-aligned.price
    if percent:result['price']=100*result.price/aligned.price.where(aligned.price.gt(1e-8))
    spot_segment=aligned.segment.ffill() # identity metadata only; prices stay missing
    result['segment']=(forward.segment.ne(forward.segment.shift())|spot_segment.ne(spot_segment.shift())).cumsum()
    result['kind']='Basis'
    return result
