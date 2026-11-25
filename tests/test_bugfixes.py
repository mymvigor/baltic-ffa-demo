import json
from pathlib import Path
import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest
from ffa.analytics import frame,options
from ffa.outlook import DEFAULTS,forecast_path,replay_current,validation_errors
from test_outlook import market_fixture


def explorer_fixture():
    rows=[]
    for start in ['2025-12-01','2026-12-01','2026-01-01','2027-01-01']:
        start=pd.Timestamp(start)
        quote_year=start.year-(start.month==1)
        for n,day in enumerate(pd.to_datetime([f'{quote_year}-11-03',f'{quote_year}-11-04',f'{quote_year}-11-05'])):
            rows.append(dict(identity=str(start.date()),quote_day=day,vessel='PMX',product='TEST',kind='BFA',currency='USD',unit='day',tenor='month',delivery_start=start,delivery_end=start+pd.offsets.MonthEnd(0),price=10000.+start.year+n))
    return frame(rows)


def explorer_app():
    return AppTest.from_string('''
import pandas as pd
from ffa.analytics import options
from ffa.workspace import MarketView
from ffa.explorer import explorer
from test_bugfixes import explorer_fixture
fixed=explorer_fixture()
view=MarketView(fixed,fixed.iloc[:0],pd.DatetimeIndex(sorted(fixed.quote_day.unique())),options(fixed,fixed.iloc[:0]))
explorer(lambda *args,**kwargs:view,(pd.Timestamp('2026-11-01'),pd.Timestamp('2026-11-05')),fixed.quote_day.min().date(),fixed.quote_day.max().date(),'regression_explorer')
''',default_timeout=20).run()


def test_fixed_overlay_keeps_cross_year_history_and_comparison_aggregation():
    app=explorer_app()
    legs=list(options(explorer_fixture(),explorer_fixture().iloc[:0]))
    select=next(w for w in app.multiselect if w.label=='Series')
    assert any('DEC25' in item for item in select.options)
    for starts in [('2025-12-01','2026-12-01'),('2026-01-01','2027-01-01')]:
        selected=[leg for leg in legs if leg[-1] in starts]
        next(w for w in app.multiselect if w.label=='Series').set_value(selected).run()
        next(w for w in app.get('segmented_control') if w.label=='Time Axis').set_value('Calendar Overlay').run()
        assert not app.exception
        spec=json.loads(app.get('plotly_chart')[0].proto.spec)
        assert len(spec['data'])==2
        assert spec['data'][0]['x']==spec['data'][1]['x']
        assert spec['data'][0]['customdata'][0][0]!=spec['data'][1]['customdata'][0][0]
    next(w for w in app.get('segmented_control') if w.label=='Compare Mode').set_value('Difference').run()
    next(w for w in app.get('segmented_control') if w.label=='Frequency').set_value('Monthly').run()
    assert not app.exception
    spec=json.loads(app.get('plotly_chart')[0].proto.spec)
    assert spec['data']
    assert 'Reference observed' not in spec['data'][0]['name']
    assert len(spec['data'][0]['customdata'][0])==2
    assert spec['layout']['xaxis']['rangeslider']['visible']


def test_date_slider_and_axis_revision_survive_model_change(monkeypatch):
    from ffa import ui
    from ffa.settlement_ui import _trajectory_axis
    figures=[]
    monkeypatch.setattr(ui.st,'plotly_chart',lambda fig,**kwargs:figures.append(fig.to_dict()))
    dates=pd.date_range('2026-01-01',periods=20)
    for values in [np.arange(20),np.arange(20)*2]:
        fig=ui.chart('USD/day')
        fig.add_scatter(x=dates,y=values)
        _trajectory_axis(fig,pd.DataFrame({'date':dates}))
        ui.display(fig,'persistent_axis')
    assert figures[0]['layout']['uirevision']==figures[1]['layout']['uirevision']
    assert figures[0]['layout']['xaxis']['rangeslider']['visible']
    assert figures[0]['layout']['dragmode']=='pan'
    assert figures[0]['layout']['legend']['y']>figures[0]['layout']['xaxis']['rangeselector']['y']


def test_partial_year_coverage_is_paired_without_filling():
    fixed=market_fixture()
    # Remove only one analogue's future segment; its reference remains valid.
    fixed=fixed[~(fixed.identity.eq('fixed-1')&fixed.quote_day.between('2025-12-01','2025-12-31'))]
    config={**DEFAULTS,'target_kind':'BFA','forecast_end':'2027-01-20'}
    path,info=forecast_path(fixed,'fixed',config)
    partial=path[path.historical_n.lt(len(info['reference_years']))]
    assert len(partial)>0 and partial.forecast.notna().all()
    assert partial.historical_years.str.split(',').map(len).eq(partial.historical_n).all()
    np.testing.assert_allclose(partial.forecast,partial.historical_mean+info['level']-partial.historical_reference)


def test_evolution_works_for_future_target_without_fixed_horizon_and_strict_vintages():
    fixed=market_fixture()
    cfg={**DEFAULTS,'target_kind':'BFA','forecast_end':'2027-02-10','years':[2025,2024,2023,2022,2021],'year_mode':'Rolling years'}
    first=replay_current(fixed,'fixed',cfg,1,target_date='2027-01-15')
    second=replay_current(fixed,'fixed',cfg,60,target_date='2027-01-15')
    assert len(first)>0
    pd.testing.assert_frame_equal(first,second)
    assert first.actual.isna().all()
    fixed.loc[fixed.identity.str.startswith('fixed-'),'available_at']=pd.NaT
    assert replay_current(fixed,'fixed',cfg,1,strict=True,target_date='2027-01-15').empty


def test_module_refresh_preserves_choices_and_updates_only_local_snapshot(monkeypatch):
    import ffa.preferences as preferences
    monkeypatch.setattr(preferences,'restore_preferences',lambda:None)
    app=AppTest.from_string('''
import streamlit as st
import app as dashboard
from ffa.analytics import frame
from ffa.demo import data
raw,relative,status=data()
def reader(*args):
    fixed,rolling=frame(raw),frame(relative)
    fixed.loc[fixed.kind.eq('Spot'),'price']+=st.session_state.get('refresh_reads',0)*100
    return fixed,rolling,status,'initial'
def clear(*args):st.session_state['refresh_reads']=st.session_state.get('refresh_reads',0)+1
reader.clear=clear
dashboard.read_live=reader
dashboard.read_manifest=lambda *args:(None,{})
dashboard.start_refresh=lambda *args:(_ for _ in ()).throw(AssertionError('Collection must not run'))
dashboard.main()
''',default_timeout=30)
    app.secrets.update({'mode':'live','github_repo':'test/repo','github_reader_token':'synthetic'})
    app.run()
    assert not app.exception
    next(w for w in app.get('segmented_control') if w.label=='Spot view').set_value('Seasonality').run()
    before=app.session_state['_market_workspace'].fixed.price.copy()
    next(w for w in app.button if w.key=='spot_local_refresh').click().run()
    assert not app.exception
    assert next(w for w in app.get('segmented_control') if w.label=='Spot view').value=='Seasonality'
    assert app.session_state['_panel_refresh_spot']==1
    assert '_panel_refresh_forecast' not in app.session_state
    assert '_panel_refresh_forward' not in app.session_state
    pd.testing.assert_series_equal(before,app.session_state['_market_workspace'].fixed.price)
    local=app.session_state['_panel_workspace_spot'][1].fixed
    original=app.session_state['_market_workspace'].fixed
    np.testing.assert_allclose(local.loc[local.kind.eq('Spot'),'price'],original.loc[original.kind.eq('Spot'),'price']+100)

def test_custom_window_peer_validation_matches_same_origin_projection():
    fixed=market_fixture()
    cfg={**DEFAULTS,'target_kind':'BFA','custom':['2026-11-02','2026-11-10'],'window':7,'years':[2025,2024,2023],'year_mode':'Rolling years','model':'Ratio'}
    rows=validation_errors(fixed,'fixed',cfg,[5])
    sample=rows[rows.contract.eq('fixed')].iloc[0]
    shift=sample.date-fixed[fixed.identity.eq('fixed')].quote_day.max()
    shifted={**cfg,'custom':[str((pd.Timestamp(d)+shift).date()) for d in cfg['custom']], 'forecast_end':str(sample.target_date.date()),'_target_dates':[str(sample.target_date.date())]}
    path,_=forecast_path(fixed,'fixed',shifted,asof=sample.date)
    assert sample.forecast==path.forecast.iloc[0]

def test_february_analogue_matches_leap_year_month_end():
    fixed=market_fixture('2026-10-23')
    fixed.loc[fixed.identity.eq('fixed-3'),'delivery_end']=pd.Timestamp('2024-02-29')
    path,info=forecast_path(fixed,'fixed',{**DEFAULTS,'years':[2023],'forecast_end':'2027-02-28'})
    assert not path.empty and info['reference_years']==[2023]
