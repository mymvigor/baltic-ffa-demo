from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
import hashlib,json
import pandas as pd
from streamlit.testing.v1 import AppTest
import ffa.preferences as preferences
import ffa.position_ui as ui
from test_positions import quotes
from test_position_sync import PrivateStore,event
from ffa.position_sync import contract_catalog


def test_single_page_default_sections_and_no_writes_while_browsing(monkeypatch):
    monkeypatch.setattr(preferences,'restore_preferences',lambda:None)
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60)
    app.secrets['mode']='demo';app.run()
    next(w for w in app.sidebar.radio if w.label=='Navigation').set_value('POSITIONS').run()
    assert not app.exception
    assert [s.value for s in app.subheader]==['Account Overview','Open Positions','Trade Entry','Account P&L','Contract P&L','Trade History']
    assert not app.get('segmented_control') and not app.expander
    assert not any(w.label in ('+ Trade','Save to GitHub','Apply to draft') for w in app.button)
    assert not any(w.label in ('Vessel','Contracts','Period','Display / export dates') for w in [*app.multiselect,*app.selectbox,*app.date_input])
    assert any('No saved transactions' in w.value for w in app.caption)
    assert app.get('download_button')
    next(w for w in app.button if w.label=='Refresh').click().run()
    assert not app.exception


def test_only_confirmed_save_updates_positions_failure_retains_book(monkeypatch):
    store=PrivateStore();fixed=quotes();catalog=contract_catalog(fixed)
    @contextmanager
    def connect(*args):yield store
    monkeypatch.setattr(ui,'connect',connect)
    monkeypatch.setattr(ui,'config',lambda:('private/repo','ffa-positions','reader','writer'))
    def grid(**kw):
        import streamlit as st
        return SimpleNamespace(batch=st.session_state.get('request') if kw['data']['mode']=='entry' else None)
    monkeypatch.setattr(ui,'grid',grid)
    app=AppTest.from_string("""
import streamlit as st
from ffa.position_ui import position_management
from test_positions import quotes
@st.fragment
def show():
 position_management(lambda *args:quotes(),pd.Timestamp('2026-11-07').date(),lambda module:0,lambda:'v1')
import pandas as pd
show()
""",default_timeout=30).run()
    assert not app.exception and store.puts==0
    app.run();assert store.puts==0
    payload={'edits':event(catalog),'deletes':{}}
    app.session_state['request']={'version':'saved-click','attempt':1,'payload':payload}
    app.run();assert not app.exception and store.puts==1
    assert app.dataframe and len(store.book['trades'])==1 and len(app.get('plotly_chart'))==4
    app.run();assert store.puts==1 # acknowledged component value cannot resubmit
    store.fail='before'
    app.session_state['request']={'version':'second-click','attempt':1,'payload':{'edits':event(catalog,'new2'),'deletes':{}}}
    app.run();assert not app.exception and len(store.book['trades'])==1
    store.fail=None
    app.session_state['request']={'version':'second-click','attempt':2,'payload':{'edits':event(catalog,'new2'),'deletes':{}}}
    app.run();assert not app.exception and len(store.book['trades'])==2


def test_direct_filters_reset_invalid_contracts_and_keep_account_risk(monkeypatch):
    from ffa.positions import empty_book
    from test_positions import trade
    fixed=quotes();smx=fixed.assign(identity='smx',vessel='SMX',product='S11TC',price=fixed.price+10)
    fixed=pd.concat([fixed,smx],ignore_index=True)
    book=empty_book();book['trades']=[trade(1,'Buy',5,100),{**trade(1,'Sell',5,100), 'id':'smx-trade','contract':'smx','vessel':'SMX','product':'S11TC'}]
    store=PrivateStore(book)
    @contextmanager
    def connect(*args):yield store
    monkeypatch.setattr(ui,'connect',connect)
    monkeypatch.setattr(ui,'config',lambda:('private/filter-test','ffa-positions','reader','writer'))
    monkeypatch.setattr(ui,'grid',lambda **kw:SimpleNamespace(batch=None))
    monkeypatch.setattr(ui,'_filter_fixture',fixed,raising=False)
    calls=[];original_calculate=ui.calculate
    def counted(*args,**kwargs):
        calls.append(1);return original_calculate(*args,**kwargs)
    monkeypatch.setattr(ui,'calculate',counted);ui.valued.clear()
    app=AppTest.from_string("""
import streamlit as st
import ffa.position_ui as ui
@st.fragment
def show():
 ui.position_management(lambda *args:ui._filter_fixture,lambda:ui._filter_fixture.quote_day.max().date(),lambda module:0,lambda:'filters')
show()
""",default_timeout=30).run()
    assert not app.exception and store.puts==0
    assert not any(w.label=='Filters' for w in app.toggle)
    vessel=next(w for w in app.get('segmented_control') if w.label=='Vessel')
    assert 'CAPE' not in vessel.options
    original=[w.value for w in app.markdown if 'font-size:1.65rem' in w.value]
    original_plots=[p.proto.spec for p in app.get('plotly_chart')]
    assert len(original_plots)==5 # three account views, one combined chart per open contract
    vessel.set_value('PMX').run()
    contracts=next(w for w in app.multiselect if w.label=='Contracts')
    contracts.set_value(['dec']).run()
    next(w for w in app.get('segmented_control') if w.label=='Vessel').set_value('SMX').run()
    assert not app.exception and next(w for w in app.multiselect if w.label=='Contracts').value==[]
    assert set(app.dataframe[0].value.Contract)=={'PMX DEC26','SMX DEC26'}
    assert [p.proto.spec for p in app.get('plotly_chart')]==original_plots
    next(w for w in app.get('segmented_control') if w.label=='Vessel').set_value('HDY').run()
    assert not app.exception and not app.multiselect
    assert any('No saved transactions for HDY' in w.value for w in app.caption)
    next(w for w in app.get('segmented_control') if w.label=='Vessel').set_value('All').run()
    next(w for w in app.selectbox if w.label=='Period').set_value('Custom dates').run()
    assert not app.exception and app.date_input
    next(w for w in app.selectbox if w.label=='Period').set_value('All dates').run()
    assert not app.exception and not app.date_input and store.puts==0
    assert [w.value for w in app.markdown if 'font-size:1.65rem' in w.value]==original
    assert [p.proto.spec for p in app.get('plotly_chart')]==original_plots
    assert len(calls)==1 # no second replay for filters or individual contracts


def test_closed_folder_preserves_history_and_reopen_restores_same_chart(monkeypatch):
    from test_position_pnl import scenario
    from test_positions import ledger
    from ffa.positions import empty_book
    trades,fixed=scenario();fixed=fixed[fixed.quote_day.le('2026-11-08')]
    book=empty_book();book['trades']=ledger();store=PrivateStore(book)
    @contextmanager
    def connect(*args):yield store
    monkeypatch.setattr(ui,'connect',connect)
    monkeypatch.setattr(ui,'config',lambda:('private/pnl-view','ffa-positions','reader','writer'))
    def grid(**kw):
        import streamlit as st
        return SimpleNamespace(batch=st.session_state.get('request') if kw['data']['mode']=='entry' else None)
    monkeypatch.setattr(ui,'grid',grid);monkeypatch.setattr(ui,'_pnl_fixture',fixed,raising=False)
    app=AppTest.from_string("""
import streamlit as st
import ffa.position_ui as ui
@st.fragment
def show():
 ui.position_management(lambda *args:ui._pnl_fixture,lambda:ui._pnl_fixture.quote_day.max().date(),lambda module:0,lambda:'pnl')
show()
""",default_timeout=30).run()
    assert not app.exception and len(app.expander)==1
    assert app.expander[0].label=='Closed Positions (1)' and len(app.get('plotly_chart'))==3
    key='_positions_'+hashlib.sha256(b'private/pnl-view:ffa-positions').hexdigest()[:20]
    app.session_state[key+'_closed_pnl']=True;app.run()
    assert not app.exception and len(app.get('plotly_chart'))==4
    before=json.loads(app.get('plotly_chart')[-1].proto.spec)
    catalog=contract_catalog(fixed,book['trades'])
    payload={'edits':event(catalog,'reopen',**{'Trade Date':'2026-11-08','Price':70,'Quantity':3,'Fees':1}),'deletes':{}}
    app.session_state['request']={'version':'reopen-click','attempt':1,'payload':payload};app.run()
    assert not app.exception and not app.expander and len(app.get('plotly_chart'))==4
    after=json.loads(app.get('plotly_chart')[-1].proto.spec)
    assert after['data'][0]['x'][0]==before['data'][0]['x'][0] # first-ever trade, not just reopen date
    assert after['layout']['uirevision'].split(':')[0]==before['layout']['uirevision'].split(':')[0]
    assert store.puts==1 and len(store.book['trades'])==7
