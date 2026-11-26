import json,math,statistics
import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest
from ffa.analytics import frame,options
from ffa.auto_correlation import (spot_history,bfa_history,predictor_inputs,predictive_stats,nonoverlap,main_contract)
from test_basis_correlation import fixture


def long_fixture():
    original=fixture();rows=[original]
    source=original[original.quote_day.dt.year.eq(2024)]
    for year in (2019,2020,2021,2022,2023):
        part=source.copy();shift=pd.DateOffset(years=year-2024)
        for field in ('quote_day','delivery_start','delivery_end'):part[field]=part[field]+shift
        part['identity']=part.apply(lambda r:'spot' if r.kind=='Spot' else str(r.delivery_start.date()),axis=1)
        # Keep original holiday/missing observations; no interpolation in the engine.
        rows.append(part)
    return pd.concat(rows,ignore_index=True).drop_duplicates(["identity","quote_day"],keep="last")


def test_automatic_years_and_family_are_valid_and_bounded():
    f=long_fixture();asof=f.quote_day.max()
    spot=spot_history(('years',),'PMX','TEST',str(asof.date()),'Price / Level',f)
    assert len(spot['corr'])==6
    assert list(spot['corr'].index)==['2026','2025','2024','2023','2022','2021']
    bfa=bfa_history(('years',),'PMX','TEST',str(asof.date()),'Price / Level',f)
    assert bfa['current']=='DEC26'
    assert len(bfa['corr'])==6 and all(name.startswith('DEC') for name in bfa['corr'])
    assert np.nanmax(np.abs(spot['corr'].to_numpy()))<=1.00000001
    # Hover dates are actual years, never the synthetic calendar year 2000.
    assert '2026' in spot['details'][0,1,1]
    assert '2025' in spot['details'][0,1,2]


def test_no_future_prices_or_future_listing_enters_factors_or_contract_selection():
    f=fixture();asof=pd.Timestamp('2026-06-15')
    first,target,_,_=predictor_inputs(f,f.iloc[:0],'PMX','TEST',asof,'Spot')
    changed=f.copy();changed.loc[changed.quote_day.gt(asof),'price']+=1e8
    extra=f[f.kind.eq('BFA')].iloc[[-1]].copy()
    extra['identity']='future-only';extra['quote_day']=pd.Timestamp('2026-07-01')
    extra['delivery_start']=pd.Timestamp('2026-08-01');extra['delivery_end']=pd.Timestamp('2026-08-31')
    changed=pd.concat([changed,extra],ignore_index=True)
    second,later,_,_=predictor_inputs(changed,changed.iloc[:0],'PMX','TEST',asof,'Spot')
    for name in first:pd.testing.assert_series_equal(first[name],second[name])
    pd.testing.assert_frame_equal(target,later)
    assert main_contract(f,asof)==main_contract(changed,asof)
    assert target.index.max()<=asof


def test_predictive_nonoverlap_and_independent_pearson():
    rng=np.random.default_rng(42);n=900;signal=rng.normal(size=n)
    price=np.r_[100.,100+np.cumsum(signal[:-1])]
    days=pd.bdate_range('2021-01-04',periods=n)
    target=pd.DataFrame(dict(price=price,segment=1),index=days)
    result=predictive_stats({'Signal':pd.Series(signal,index=days)},target,{'Signal':'Known value at t'},'test')
    for h in (1,5,10,20):
        origins=list(range(0,n-h,h))
        x=[float(signal[i]) for i in origins];y=[float(price[i+h]-price[i]) for i in origins]
        mx,my=statistics.fmean(x),statistics.fmean(y)
        expected=sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))
        assert result['counts'].loc['Signal',f'{h}D']==len(origins)
        np.testing.assert_allclose(result['corr'].loc['Signal',f'{h}D'],expected,rtol=1e-12)
    pair=pd.DataFrame(dict(x=signal[:-20]),index=days[:-20]);ends=pd.Series(days,index=days).shift(-20)
    selected=nonoverlap(pair,ends)
    assert all(selected.index[i]>=ends.loc[selected.index[i-1]] for i in range(1,len(selected)))


def test_four_heatmaps_default_controls_and_independent_cache(monkeypatch):
    import ffa.auto_correlation as automatic
    automatic.spot_history.clear();automatic.bfa_history.clear()
    calls=[];original=automatic._history_frames
    def trace(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(automatic,'_history_frames',trace)
    app=AppTest.from_string('''
import pandas as pd
from test_basis_correlation import fixture
from ffa.analytics import options
from ffa.workspace import MarketView
from ffa.auto_correlation import automatic_correlation_view
f=fixture();v=MarketView(f,f.iloc[:0],pd.DatetimeIndex(sorted(f.quote_day.unique())),options(f,f.iloc[:0]))
automatic_correlation_view(lambda *args,**kwargs:v,f.quote_day.min().date(),f.quote_day.max().date(),'automatic_test',('cache-test',),'PMX','TEST',{'PMX':('TEST',)})
''',default_timeout=45).run()
    assert not app.exception
    charts=app.get('plotly_chart');assert len(charts)==4
    assert all(json.loads(p.proto.spec)['data'][0]['type']=='heatmap' for p in charts)
    assert not app.multiselect
    assert not any(w.label in ('Series','Alignment','Frequency','View') for w in app.get('segmented_control'))
    assert not any(w.label=='Predictive Contract' for w in app.selectbox)
    before=len(calls);assert before>0
    next(w for w in app.get('segmented_control') if w.label=='Predictive Target').set_value('BFA').run()
    assert not app.exception and len(app.get('plotly_chart'))==4
    assert any(w.label=='Predictive Contract' for w in app.selectbox)
    assert len(calls)==before
    next(w for w in app.get('segmented_control') if w.label=='Historical Comparison').set_value('Price Changes').run()
    assert not app.exception and len(calls)>before
    assert not app.dataframe
