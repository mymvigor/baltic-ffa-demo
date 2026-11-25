import json
import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from ffa.outlook import DEFAULTS,forecast_path,forecast_score,replay_current
from test_outlook import market_fixture


def test_spot_ratio_and_difference_share_historical_cohort_and_preserve_actuals():
    fixed=market_fixture()
    config={**DEFAULTS,'target_kind':'Spot','forecast_end':'2027-02-20','model':'Ratio'}
    ratio,info=forecast_path(fixed,'spot:PMX:TEST',config)
    assert not ratio.empty
    np.testing.assert_allclose(ratio.forecast,ratio.historical_mean*info['level']/ratio.historical_reference,equal_nan=True)
    difference,_=forecast_path(fixed,'spot:PMX:TEST',{**config,'model':'Difference'})
    assert not np.allclose(ratio.forecast,difference.forecast,equal_nan=True)
    assert ratio.target_date.min()>info['asof']
    assert (ratio.loc[ratio.historical_n.lt(len(info['reference_years'])),'forecast'].isna()).all()


def test_forecast_score_flat_directions_zero_baseline_and_sample_guard():
    rows=pd.DataFrame(dict(anchor=[100.]*12,actual=[110.]*12,forecast=[108.]*12,momentum=[105.]*12,majority=[1.]*12))
    score=forecast_score(rows)
    assert score['skill']==pytest.approx(80.)
    assert score['direction']==1.
    assert score['mae']==2.
    assert np.isnan(forecast_score(rows.iloc[:3])['skill'])
    rows['actual']=100.
    assert np.isnan(forecast_score(rows)['skill'])
    assert forecast_score(rows)['direction']==0.


def test_unified_spot_controls_review_and_evolution_render():
    app=AppTest.from_string('''
import pandas as pd
from test_outlook import market_fixture
from ffa.forecast_ui import unified_forecast
asof=pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()-pd.offsets.BDay(1)
unified_forecast(market_fixture(asof),("new",),"PMX","TEST","unified_test","Spot",synthetic=True)
''',default_timeout=60).run()
    assert not app.exception
    assert len(app.metric)==4
    assert app.get('plotly_chart')
    next(w for w in app.get('segmented_control') if w.label=='Model').set_value('Ratio').run()
    assert not app.exception
    for view in ('Forecast vs Actual','Forecast Error','Forecast Evolution'):
        next(w for w in app.get('segmented_control') if w.label=='Chart view').set_value(view).run()
        assert not app.exception
    next(w for w in app.get('segmented_control') if w.label=='Review data').set_value('Saved Forecasts').run()
    assert not app.exception
    assert any('No actual issued records' in w.value for w in app.info)
