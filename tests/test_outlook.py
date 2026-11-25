import base64
from copy import deepcopy
import gzip
import json

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from ffa.analytics import frame
from ffa.forecast_store import read_records, save_records
from ffa.outlook import (DEFAULTS, apply_judgement, evaluated_record, forecast_path,
                         prediction_record, validation_errors)


def market_fixture(asof='2026-11-23'):
    asof = pd.Timestamp(asof).normalize()
    days = pd.bdate_range(end=asof, periods=85)
    expiry = (asof + pd.DateOffset(months=4)) + pd.offsets.MonthEnd(0)
    rows = []
    for i, day in enumerate(days):
        for identity, kind in [('fixed','BFA'),('spot','Spot')]:
            rows.append(dict(identity=identity, quote_day=day, vessel='PMX', product='TEST',kind=kind,
                             currency='USD',unit='day',tenor='month' if kind=='BFA' else 'spot',
                             delivery_start=expiry.replace(day=1) if kind=='BFA' else None,
                             delivery_end=expiry if kind=='BFA' else None, price=20000.+i*10,
                             received_at=(day.tz_localize('UTC')+pd.Timedelta(hours=16)).isoformat()))
    return frame(rows)


def test_mean_path_covers_entire_fixed_contract_and_uses_only_past_quotes():
    fixed = market_fixture()
    cutoff = pd.Timestamp('2026-11-10')
    path, info = forecast_path(fixed,'fixed',asof=cutoff)
    assert len(path) > 20
    assert path.target_date.min() > cutoff
    assert path.target_date.max() <= info['expiry']
    observed = fixed[fixed.identity.eq('fixed') & fixed.quote_day.le(cutoff)].price.tail(20)
    assert path.forecast.eq(observed.mean()).all()
    changed = fixed.copy()
    changed.loc[changed.quote_day.gt(cutoff),'price'] = 1e8
    after, _ = forecast_path(changed,'fixed',asof=cutoff)
    pd.testing.assert_frame_equal(path,after)
    missing = fixed[~(fixed.identity.eq('fixed') & fixed.quote_day.eq('2026-11-06'))]
    result, _ = forecast_path(missing,'fixed',asof=cutoff)
    expected = missing[missing.identity.eq('fixed') & missing.quote_day.le(cutoff)].price.tail(20).mean()
    assert result.forecast.eq(expected).all()


def test_subjective_path_changes_future_only_and_expires():
    path, _ = forecast_path(market_fixture(),'fixed')
    config = {**DEFAULTS,'judgement':'Bearish','strength':.3,'maximum_pct':10.,
              'judgement_from':'2026-11-23','judgement_until':'2026-12-10','fade':True}
    adjusted = apply_judgement(path,config)
    assert adjusted[0] < path.forecast.iloc[0]
    assert np.all(adjusted >= path.forecast*.97)
    np.testing.assert_allclose(adjusted[path.target_date.gt('2026-12-10')],path.loc[path.target_date.gt('2026-12-10'),'forecast'])
    config['strength'] = .6
    assert apply_judgement(path,config)[0] < adjusted[0]


def test_issued_path_has_genuine_creation_time_and_future_targets_only():
    fixed = market_fixture('2026-11-10')
    path, info = forecast_path(fixed,'fixed')
    record = prediction_record(path,info,'fixed',DEFAULTS,'source-v1','2026-11-24T12:00:00Z')
    assert record['created_at'].startswith('2026-11-24')
    assert all(pd.Timestamp(point['target_date']) > pd.Timestamp('2026-11-24') for point in record['points'])
    original = deepcopy(record)
    extra = fixed.iloc[[0]].copy()
    extra['identity'],extra['kind'],extra['quote_day'],extra['price'] = 'fixed','BFA',pd.Timestamp(record['points'][0]['target_date']),21000.
    evaluated = evaluated_record(record,pd.concat([fixed,extra],ignore_index=True))
    assert evaluated.actual.notna().sum() == 1
    assert record == original


class Response:
    def __init__(self,status,value=None):
        self.status_code,self.value = status,value
    def json(self):
        return self.value


class MemoryStore:
    def __init__(self):
        self.files={}
        self.collision=False
    def read_file(self,path):
        return self.files.get(path,(None,None))[0]
    def request(self,method,path,**kwargs):
        path=path.removeprefix('/contents/')
        assert path.startswith('forecasts/')
        if method == 'GET':
            if path not in self.files:
                return Response(404)
            raw,sha=self.files[path]
            return Response(200,dict(encoding='base64',content=base64.b64encode(raw).decode(),sha=sha))
        body=kwargs['json']
        if self.collision:
            self.collision=False
            return Response(409)
        if body.get('sha') != self.files.get(path,(None,None))[1]:
            return Response(409)
        self.files[path]=(base64.b64decode(body['content']),str(len(self.files)+len(body['content'])))
        return Response(200)


def test_private_journal_is_idempotent_and_never_overwrites_issued_values():
    store=MemoryStore()
    path,info=forecast_path(market_fixture(),'fixed')
    record=prediction_record(path,info,'fixed',DEFAULTS,'source','2026-11-24T12:00:00Z')
    store.collision=True
    save_records(store,[record])
    modified=deepcopy(record)
    modified['points'][0]['forecast']=1.
    save_records(store,[modified])
    saved=read_records(store,'fixed')
    assert saved[record['id']]['points'][0]['forecast'] == record['points'][0]['forecast']
    assert len(saved) == 1
    assert read_records(store,'fixed',day='2026-11-24') == saved
    assert read_records(store,'fixed',day='2026-11-23') == {}
    assert all(name.startswith('forecasts/') for name in store.files)


def test_mean_validation_respects_time_cutoff_and_delivery_end():
    from test_predictive import history
    fixed=history()
    asof=pd.Timestamp('2024-11-20')
    rows=validation_errors(fixed,'2025-01-01',DEFAULTS,(1,5,20),asof)
    assert not rows.empty
    assert rows.target_date.le(asof).all()
    ends=fixed.drop_duplicates('identity').set_index('identity').delivery_end
    assert (rows.target_date <= rows.contract.map(ends)).all()
    changed=fixed.copy()
    changed.loc[changed.quote_day.gt(asof),'price'] += 1e7
    pd.testing.assert_frame_equal(rows,validation_errors(changed,'2025-01-01',DEFAULTS,(1,5,20),asof))


def test_future_chart_and_subjective_controls_update_without_rewriting_base():
    result=AppTest.from_string('''
import pandas as pd
from test_outlook import market_fixture
from ffa.outlook_ui import outlook_view, _memory
from ffa.outlook import DEFAULTS, forecast_path, prediction_record
asof = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()-pd.offsets.BDay(1)
fixed = market_fixture(asof)
issued = pd.Timestamp.now(tz="UTC").normalize()-pd.Timedelta(days=30)
past_path, info = forecast_path(fixed, "fixed", asof=issued.tz_localize(None))
record = prediction_record(past_path, info, "fixed", DEFAULTS, "test-source", issued)
_memory("future_test").setdefault(record["id"], record)
outlook_view(fixed, ("test",), "PMX", "TEST", "future_test", synthetic=True)
''',default_timeout=30).run()
    assert not result.exception
    spec=json.loads(result.get('plotly_chart')[0].proto.spec)
    assert any(trace.get('name') == 'Future mean forecast' for trace in spec['data'])
    next(w for w in result.get('segmented_control') if w.label=='BFA path').set_value('Exploratory scenario').run()
    next(w for w in result.get('segmented_control') if w.label=='Market judgement').set_value('Bearish').run()
    assert not result.exception
    def values(name):
        spec=json.loads(result.get('plotly_chart')[0].proto.spec)
        return next(trace['y'] for trace in spec['data'] if trace.get('name')==name)
    base=values('Future mean forecast')
    before=values('Exploratory scenario')
    result.number_input[0].set_value(.6).run()
    assert not result.exception
    assert values('Future mean forecast') == base
    assert values('Exploratory scenario') != before
    next(w for w in result.get('segmented_control') if w.label=='Forecast view').set_value('Saved forecast review').run()
    assert not result.exception
    assert any('matched dates' in w.value for w in result.caption)
    next(w for w in result.get('segmented_control') if w.label=='Chart mode').set_value('Forecast error').run()
    assert not result.exception
    assert any('Issued forecast' in str(p.proto.spec) for p in result.get('plotly_chart'))


def test_daily_worker_keeps_saved_judgement_deadline_and_generates_real_future_records():
    import importlib.util
    from pathlib import Path
    script=Path(__file__).resolve().parents[3]/'forecast_daily.py'
    if not script.exists():
        pytest.skip('Private worker is packaged separately from the public app')
    spec=importlib.util.spec_from_file_location('daily_forecast_worker',script)
    worker=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    config={**DEFAULTS,'judgement':'Bearish','strength':.3,'judgement_from':'2026-11-24',
            'judgement_until':'2026-11-27','fade':True}
    active={'fixed':dict(vessel='PMX',product='TEST',config=config)}
    records=worker.generate(market_fixture(),active,now='2026-11-25T02:00:00Z')
    assert len(records) == 2
    saved=next(row for row in records if row['config']['judgement']=='Bearish')
    assert saved['config']['judgement_until'] == '2026-11-27'
    assert all(pd.Timestamp(point['target_date']) > pd.Timestamp('2026-11-25') for point in saved['points'])
    assert all(point['scenario'] == point['forecast'] for point in saved['points'] if point['target_date'] > '2026-11-27')
