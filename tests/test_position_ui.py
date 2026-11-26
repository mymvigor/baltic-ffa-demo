from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
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
    assert [s.value for s in app.subheader]==['Account Overview','Open Positions','Trade Entry','P&L Performance','Trade History']
    assert not app.get('segmented_control') and not app.expander
    assert not any(w.label in ('+ Trade','Save to GitHub','Apply to draft') for w in app.button)
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
    assert app.dataframe and len(store.book['trades'])==1 and len(app.get('plotly_chart'))==3
    app.run();assert store.puts==1 # acknowledged component value cannot resubmit
    store.fail='before'
    app.session_state['request']={'version':'second-click','attempt':1,'payload':{'edits':event(catalog,'new2'),'deletes':{}}}
    app.run();assert not app.exception and len(store.book['trades'])==1
    store.fail=None
    app.session_state['request']={'version':'second-click','attempt':2,'payload':{'edits':event(catalog,'new2'),'deletes':{}}}
    app.run();assert not app.exception and len(store.book['trades'])==2
