import json
import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest
from ffa.outlook import DEFAULTS,future_grid,forecast_path,forecast_score,_aligned_values
from test_outlook import market_fixture


def test_spot_and_bfa_publication_calendars_are_distinct_and_expiry_is_respected():
    spot,_=future_grid('2026-12-23','2027-01-05','Spot')
    bfa,_=future_grid('2026-12-23','2027-01-05','BFA')
    assert pd.Timestamp('2026-12-29') not in spot
    assert pd.Timestamp('2026-12-29') in bfa
    assert pd.Timestamp('2026-12-28') not in bfa
    # Same contract's indicative path cannot continue past final index publication.
    f=market_fixture('2026-08-23')
    path,info=forecast_path(f,'fixed',DEFAULTS)
    assert info['expiry']==pd.Timestamp('2026-12-24')
    assert path.target_date.max()<=info['expiry']


def test_nearest_quote_tolerance_uses_nanoseconds_and_does_not_extend_coverage():
    values=pd.Series([100.,200.],index=pd.DatetimeIndex(['2025-01-10','2025-01-20']).as_unit('us'))
    targets=pd.DatetimeIndex(['2025-01-09','2025-01-11','2025-01-15','2025-01-21']).as_unit('ns')
    result=_aligned_values(values,targets)
    assert np.isnan(result[[0,2,3]]).all()
    assert result[1]==100.


def test_score_filters_unobserved_targets_before_counting():
    rows=pd.DataFrame(dict(anchor=[100.]*11,actual=[110.]*10+[np.nan],forecast=[108.]*11,momentum=[105.]*11,majority=[1.]*11))
    result=forecast_score(rows)
    assert result['samples']==10
    assert result['skill']==80.
    assert result['direction']==1.


def test_simple_controls_and_subjective_curve_update_without_faking_replay():
    app=AppTest.from_string('''
import pandas as pd
from test_outlook import market_fixture
from ffa.forecast_ui import unified_forecast
asof=pd.Timestamp.now(tz='UTC').tz_localize(None).normalize()-pd.offsets.BDay(1)
unified_forecast(market_fixture(asof),('linked',),'PMX','TEST','linked_test','Spot',synthetic=True)
''',default_timeout=60).run()
    assert not app.exception
    assert next(w for w in app.get('segmented_control') if w.label=='Prediction Horizon').value==5
    assert not any(w.label in ('Evaluation Horizon','Forecast model') for w in app.get('segmented_control'))
    assert not any(w.label=='Forecast through' for w in app.date_input)
    next(w for w in app.get('segmented_control') if w.label=='Model').set_value('Exploratory').run()
    next(w for w in app.get('segmented_control') if w.label=='Market judgement').set_value('Bullish').run()
    def values():
        spec=json.loads(app.get('plotly_chart')[0].proto.spec)
        return {trace['name']:trace['y'] for trace in spec['data']}
    before=values()
    summary_before=' '.join(w.value for w in app.markdown if 'Expected Change' in w.value)
    metrics_before=[w.value for w in app.metric]
    next(w for w in app.number_input if w.label=='Judgement strength').set_value(.8).run()
    assert not app.exception
    after=values()
    assert before['Exploratory scenario']!=after['Exploratory scenario']
    assert before['Difference forecast']==after['Difference forecast']
    assert summary_before!=' '.join(w.value for w in app.markdown if 'Expected Change' in w.value)
    assert metrics_before==[w.value for w in app.metric]
    assert any('base model' in w.value for w in app.caption)
    next(w for w in app.get('segmented_control') if w.label=='Prediction Horizon').set_value(10).run()
    assert not app.exception
    assert any('10 Trading Days Ahead' in w.value for w in app.markdown)

def test_forward_snapshot_date_is_latest_not_after_target_in_either_order():
    from ffa.ui import as_of
    days=list(pd.to_datetime(['2026-11-02','2026-11-03','2026-11-05']))
    assert as_of(days,pd.Timestamp('2026-11-04'))==pd.Timestamp('2026-11-03')
    assert as_of(days[::-1],pd.Timestamp('2026-11-04'))==pd.Timestamp('2026-11-03')
    assert as_of(days,pd.Timestamp('2026-11-01')) is None


def test_flat_direction_uses_same_roundoff_tolerance_as_conclusion():
    rows=pd.DataFrame(dict(anchor=[100.]*10,actual=[100.]*10,forecast=[100.+1e-10]*10,momentum=[100.]*10,majority=[0.]*10))
    result=forecast_score(rows)
    assert result['direction']==1.
    assert result['majority_accuracy']==1.
    assert np.isnan(result['skill'])

def test_evolution_rejects_future_non_publication_date():
    from ffa.outlook import replay_current
    f=market_fixture()
    rows=replay_current(f,'fixed',{**DEFAULTS,'target_kind':'BFA'},5,target_date='2026-12-27')
    assert rows.empty


def test_auto_spot_range_rolls_forward_without_changing_saved_config():
    import importlib.util
    from copy import deepcopy
    from pathlib import Path
    import pytest
    script=Path(__file__).resolve().parents[3]/'forecast_daily.py'
    if not script.exists():pytest.skip('Private worker is a separate package')
    spec=importlib.util.spec_from_file_location('range_worker',script)
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    config={**DEFAULTS,'target_kind':'Spot','range_mode':'Auto','forecast_end':'2026-12-01','years':[2025,2024,2023]}
    active={'spot:PMX:TEST':dict(vessel='PMX',product='TEST',config=config)};original=deepcopy(active)
    records=worker.generate(market_fixture(),active,now='2026-11-25T02:00:00Z')
    record=next(r for r in records if r['contract']=='spot:PMX:TEST' and r['config'].get('range_mode')=='Auto')
    assert record['config']['forecast_end']=='2027-02-25'
    assert active==original

def test_expired_contract_review_does_not_depend_on_a_remaining_future_path():
    app=AppTest.from_string('''
import pandas as pd
from test_outlook import market_fixture
from ffa.forecast_ui import unified_forecast
f=market_fixture('2025-11-23')
row=f[f.identity.eq('fixed')].iloc[[-1]].copy()
row['quote_day']=row.delivery_end
f=pd.concat([f,row],ignore_index=True)
unified_forecast(f,('expired',),'PMX','TEST','expired_test','BFA',synthetic=True)
''',default_timeout=60).run()
    assert not app.exception
    next(w for w in app.get('segmented_control') if w.label=='Chart view').set_value('Forecast vs Actual').run()
    assert not app.exception
    assert app.get('plotly_chart')
    assert len(app.metric)==4
