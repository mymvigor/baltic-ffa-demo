import json,math,statistics
import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from ffa.analytics import frame,options,timeline
from ffa.series import inventory,basis_frame,source_frame
from ffa.basis import implied_remaining,historical_position
from ffa.correlation import matrix_stats,rolling_stats,future_change,lead_lag_stats,transform,frequency_frame,align_context,clip_native,resolve


def fixture():
    rows=[]
    for year in (2024,2025,2026):
        days=pd.bdate_range(f'{year}-01-02',f'{year}-11-23' if year==2026 else f'{year}-12-24')
        for n,d in enumerate(days):
            spot=14000+1000*np.sin(n/11)+n*4
            base=dict(quote_day=d,vessel='PMX',product='TEST',currency='USD',unit='day')
            rows.append(dict(base,identity='spot',kind='Spot',tenor='spot',price=spot))
            for month,premium in [(12,400),(1,700)]:
                start=pd.Timestamp(year+(month==1),month,1)
                rows.append(dict(base,identity=str(start.date()),kind='BFA',tenor='month',delivery_start=start,delivery_end=start+pd.offsets.MonthEnd(),price=spot+premium+200*np.cos(n/9)))
    f=frame(rows)
    return f


def pearson(a,b):
    ma,mb=statistics.fmean(a),statistics.fmean(b)
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(sum((x-ma)**2 for x in a)*sum((y-mb)**2 for y in b))


def test_basis_sign_denominator_roll_and_implied_remaining():
    days=pd.bdate_range('2026-11-02',periods=6)
    a=pd.DataFrame(dict(price=[110,112,113,150,151,152],segment=[1,1,1,2,2,2],roll_from=[None]*3+['old',None,None],roll_to=[None]*3+['new',None,None]),index=days)
    b=a.assign(price=[100,100,0,120,np.nan,120],segment=1)
    result=basis_frame(a,b)
    assert result.price.iloc[0]==10
    assert result.price.iloc[3]==30
    percent=basis_frame(a,b,True)
    assert percent.price.iloc[0]==10
    assert np.isnan(percent.price.iloc[2]) and np.isnan(percent.price.iloc[4])
    assert np.isnan(transform(result,'Absolute Change').price.iloc[3])
    november=pd.bdate_range('2026-11-01','2026-11-30')
    spot=pd.Series(100.,index=november[:10])
    value=implied_remaining(120.,spot,'2026-11',november[9])
    assert value['value']==pytest.approx((len(november)*120-1000)/(len(november)-10))
    assert 'reason' in implied_remaining(120.,spot.iloc[:-1],'2026-11',november[9])


def test_common_sample_matrix_and_lead_direction_independent_calculation():
    n=180;rng=np.random.default_rng(42);signal=rng.normal(size=n)
    target=np.r_[100.,100.+np.cumsum(signal[:-1])]
    days=pd.bdate_range('2026-01-01',periods=n)
    x=pd.DataFrame(dict(price=signal,segment=1),index=days)
    y=pd.DataFrame(dict(price=target,segment=1),index=days)
    values=pd.DataFrame({'a':signal,'b':2*signal+3,'c':-signal},index=days);values.iloc[5,2]=np.nan
    corr,count=matrix_stats(values)
    assert count==n-1 and corr.iloc[0,1]==pytest.approx(1.) and corr.iloc[0,2]==pytest.approx(-1.)
    result=lead_lag_stats(x,y)
    assert result.n.nunique()==1
    assert result.loc[result.horizon.eq(1),'corr'].iloc[0]==pytest.approx(1.)
    keep=n-20
    expected=pearson(signal[:keep].tolist(),(target[5:5+keep]-target[:keep]).tolist())
    assert result.loc[result.horizon.eq(5),'corr'].iloc[0]==pytest.approx(expected)
    y.loc[days[50],'price']=np.nan
    assert np.isnan(future_change(y,5).loc[days[48]])
    y.loc[days[90]:,'segment']=2
    assert np.isnan(future_change(y,5).loc[days[88]])


def test_changes_and_frequency_do_not_count_roll_jumps():
    days=pd.bdate_range('2026-01-05',periods=90)
    a=pd.DataFrame(dict(price=np.arange(90.)+100,segment=np.r_[np.ones(43),np.full(47,2)],observed_day=days),index=days)
    a.loc[days[43]:,'price']+=10000
    changes=transform(a,'Absolute Change')
    assert np.isnan(changes.price.iloc[43])
    assert changes.price.dropna().eq(1.).all()
    weekly=frequency_frame(a,'Weekly')
    assert weekly.price.isna().any()
    corr,count=rolling_stats(changes,changes,60)
    assert count.iloc[-1]==59
    assert corr.isna().all() # constant changes have undefined correlation


def test_overlay_matches_delivery_season_and_censors_future_season():
    f=fixture();raw=options(f,f.iloc[:0]);bounds=(f.quote_day.min(),f.quote_day.max())
    c,l=inventory(raw,bounds,monthly=False)
    selected=[k for k in c if k[2]=='BFA' and k[6]=='Fixed' and c[k][1].delivery_start.iloc[0] in (pd.Timestamp('2025-12-01'),pd.Timestamp('2026-12-01'))]
    asof=f.quote_day.max();ref,offset=align_context(selected,c,f,'Calendar Overlay',asof)
    for k in selected:
        data,leg,formula=resolve(k,c,f,bounds)
        clipped,anchor=clip_native(data,leg,c,'Calendar Overlay',asof,ref,offset)
        assert clipped.index[-1].month==11 and clipped.index[-1].day<=asof.day
    january=next(k for k in c if k[2]=='BFA' and k[6]=='Fixed' and c[k][1].delivery_start.iloc[0]==pd.Timestamp('2027-01-01'))
    with pytest.raises(ValueError):align_context([selected[0],january],c,f,'Calendar Overlay',asof)
    # MA uses prior-year warmup; the selected year is clipped afterwards.
    spotyear=next(k for k in c if k[2]=='Spot' and len(k)==10 and k[-1]=='2025')
    values,leg,_=resolve((spotyear,'MA20'),c,f,bounds)
    part,_=clip_native(values,leg,c,'Actual Date',asof,ref,offset)
    assert np.isfinite(part.price.iloc[0])


def correlation_app():
    return AppTest.from_string('''
import pandas as pd
from test_basis_correlation import fixture
from ffa.analytics import options
from ffa.workspace import MarketView
from ffa.correlation import correlation_view
f=fixture();v=MarketView(f,f.iloc[:0],pd.DatetimeIndex(sorted(f.quote_day.unique())),options(f,f.iloc[:0]))
correlation_view(lambda *args,**kwargs:v,f.quote_day.min().date(),f.quote_day.max().date(),'correlation_test')
''',default_timeout=30).run()


def test_three_views_and_derived_series_render_without_tables():
    app=correlation_app();assert not app.exception
    f=fixture();c,l=inventory(options(f,f.iloc[:0]),(f.quote_day.min(),f.quote_day.max()),monthly=False)
    spot=next(k for k in c if k[2]=='Spot' and len(k)==8)
    dec=next(k for k in c if k[2]=='BFA' and c[k][1].delivery_start.iloc[0]==pd.Timestamp('2026-12-01'))
    next(w for w in app.multiselect if w.label=='Series').set_value([spot,dec]).run()
    for view in ('Correlation Matrix','Rolling Correlation','Lead/Lag Correlation'):
        next(w for w in app.get('segmented_control') if w.label=='View').set_value(view).run()
        assert not app.exception and app.get('plotly_chart')
    next(w for w in app.get('segmented_control') if w.label=='View').set_value('Correlation Matrix').run()
    next(w for w in app.multiselect if w.label=='Series').set_value([(dec,'Basis'),(spot,'MA5 − MA20')]).run()
    assert not app.exception and app.get('plotly_chart') and not app.dataframe
    next(w for w in app.get('segmented_control') if w.label=='Frequency').set_value('Monthly').run()
    assert not app.exception

def test_basis_uses_explorer_chart_for_fixed_and_calendar_overlay():
    app=AppTest.from_string('''
import pandas as pd
from test_basis_correlation import fixture
from ffa.analytics import options
from ffa.workspace import MarketView
from ffa.explorer import explorer
f=fixture();v=MarketView(f,f.iloc[:0],pd.DatetimeIndex(sorted(f.quote_day.unique())),options(f,f.iloc[:0]))
explorer(lambda *args,**kwargs:v,(f.quote_day.min(),f.quote_day.max()),f.quote_day.min().date(),f.quote_day.max().date(),'basis_test')
''',default_timeout=30).run()
    next(w for w in app.get('segmented_control') if w.label=='Compare Mode').set_value('Basis').run()
    assert not app.exception
    f=fixture();c,l=inventory(options(f,f.iloc[:0]),(f.quote_day.min(),f.quote_day.max()))
    legs=[k for k in c if k[2]=='BFA' and k[6]=='Fixed' and c[k][1].delivery_start.iloc[0] in (pd.Timestamp('2025-12-01'),pd.Timestamp('2026-12-01'))]
    next(w for w in app.multiselect if w.label=='Series').set_value(legs).run()
    for axis in ('Calendar Overlay','Actual Date'):
        next(w for w in app.get('segmented_control') if w.label=='Time Axis').set_value(axis).run()
        for mode in ('Basis %','Basis'):
            next(w for w in app.get('segmented_control') if w.label=='Compare Mode').set_value(mode).run()
            assert not app.exception
            assert len(app.get('plotly_chart'))==1
            spec=json.loads(app.get('plotly_chart')[0].proto.spec)
            assert len(spec['data'])==2
            assert all(trace['name'].startswith(mode+' ·') for trace in spec['data'])
    next(w for w in app.get('segmented_control') if w.label=='Frequency').set_value('Monthly').run()
    assert not app.exception and app.get('plotly_chart')

def test_weekly_overlay_uses_common_calendar_bins_and_real_date_metadata():
    from ffa.correlation import mapped
    f=fixture();c,l=inventory(options(f,f.iloc[:0]),(f.quote_day.min(),f.quote_day.max()),monthly=False)
    keys=[k for k in c if k[2]=='Spot' and len(k)==10 and k[-1] in ('2025','2026')]
    asof=f.quote_day.max();ref,offset=align_context(keys,c,f,'Calendar Overlay',asof)
    values=[]
    for k in keys:
        data,leg,_=resolve(k,c,f,(f.quote_day.min(),f.quote_day.max()))
        data,anchor=clip_native(data,leg,c,'Calendar Overlay',asof,ref,offset)
        weeks=frequency_frame(mapped(data,anchor),'Weekly')
        assert weeks.observed_day.dropna().dt.year.eq(int(k[-1])).all()
        values.append(transform(weeks,'Absolute Change').price.rename(k[-1]))
    corr,n=matrix_stats(pd.concat(values,axis=1))
    assert n>=30 and np.isfinite(corr.iloc[0,1])


def test_basis_missing_spot_does_not_create_a_false_contract_roll():
    dates=pd.bdate_range('2026-01-05',periods=25)
    forward=pd.DataFrame(dict(price=np.arange(25.)+100,segment=1),index=dates)
    spot=pd.DataFrame(dict(price=np.arange(24.)+80,segment=1),index=dates.delete(10))
    result=basis_frame(forward,spot)
    assert result.segment.nunique()==1
    assert np.isnan(result.price.iloc[10])
    assert np.isnan(transform(result,'Absolute Change').price.iloc[11])

def test_pairwise_fallback_keeps_valid_years_without_ranking_different_samples():
    from ffa.correlation import matrix_analysis
    x=np.sin(np.arange(140)/7)
    values=pd.DataFrame({'a':x,'b':x*2+1,'c':-x})
    values.loc[:49,'a']=np.nan
    values.loc[50:99,'b']=np.nan
    values.loc[100:,'c']=np.nan
    assert values.dropna().empty
    corr,n,shared=matrix_analysis(values)
    assert not shared
    assert n.loc['a','b']==40 and n.loc['a','c']==50
    assert corr.loc['a','b']==pytest.approx(1.)
    assert corr.loc['a','c']==pytest.approx(-1.)


def test_rolling_target_does_not_erase_valid_short_horizons():
    rng=np.random.default_rng(5);signal=rng.normal(size=180)
    prices=np.r_[100.,100+np.cumsum(signal[:-1])]
    days=pd.bdate_range('2026-01-01',periods=180)
    source=pd.DataFrame(dict(price=signal,segment=1),index=days)
    target=pd.DataFrame(dict(price=prices,segment=np.arange(180)//22),index=days)
    result=lead_lag_stats(source,target)
    assert not result.shared.any()
    assert result.loc[result.horizon.eq(1),'corr'].iloc[0]==pytest.approx(1.)
    assert result.loc[result.horizon.eq(20),'n'].iloc[0]<30
    assert np.isnan(result.loc[result.horizon.eq(20),'corr'].iloc[0])

def test_expired_only_contract_analogues_keep_the_current_seasonal_cutoff():
    f=fixture();c,l=inventory(options(f,f.iloc[:0]),(f.quote_day.min(),f.quote_day.max()))
    old=[k for k in c if k[2]=='BFA' and k[6]=='Fixed' and c[k][1].delivery_start.iloc[0] in (pd.Timestamp('2024-12-01'),pd.Timestamp('2025-12-01'))]
    asof=f.quote_day.max();ref,offset=align_context(old,c,f,'Calendar Overlay',asof)
    assert ref==2026 and offset==0
    for k in old:
        data,leg,_=resolve(k,c,f,(f.quote_day.min(),f.quote_day.max()))
        clipped,anchor=clip_native(data,leg,c,'Calendar Overlay',asof,ref,offset)
        assert clipped.index.max().month==11
        assert clipped.index.max().day<=asof.day

def test_lead_lag_requires_price_target_and_assigns_factor_signal_automatically():
    app=correlation_app();f=fixture();c,l=inventory(options(f,f.iloc[:0]),(f.quote_day.min(),f.quote_day.max()),monthly=False)
    spot=next(k for k in c if k[2]=='Spot' and len(k)==8)
    dec=next(k for k in c if k[2]=='BFA' and c[k][1].delivery_start.iloc[0]==pd.Timestamp('2026-12-01'))
    next(w for w in app.multiselect if w.label=='Series').set_value([spot,(dec,'Basis')]).run()
    next(w for w in app.get('segmented_control') if w.label=='View').set_value('Lead/Lag Correlation').run()
    assert not app.exception and app.get('plotly_chart')
    assert any('future price target' in w.value and 'Basis' in w.value for w in app.caption)
    next(w for w in app.multiselect if w.label=='Series').set_value([(spot,'MA5'),(dec,'Basis')]).run()
    assert not app.exception
    assert any('price series as the future-price target' in w.value for w in app.info)
