from pathlib import Path
from streamlit.testing.v1 import AppTest
import ffa.preferences as preferences


def test_position_entry_edit_report_and_market_changes(monkeypatch):
    monkeypatch.setattr(preferences,'restore_preferences',lambda:None)
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60)
    app.secrets['mode']='demo';app.run()
    assert not app.exception
    assert len(app.dataframe)>=2
    next(w for w in app.get('segmented_control') if w.label=='Chart').set_value('Daily Change').run()
    assert not app.exception
    next(w for w in app.sidebar.radio if w.label=='Navigation').set_value('POSITIONS').run()
    assert not app.exception
    next(w for w in app.get('segmented_control') if w.label=='Position view').set_value('Transactions').run()
    next(w for w in app.button if w.label=='+ Trade').click().run()
    assert not app.exception
    next(w for w in app.number_input if w.label=='Price').set_value(15000)
    next(w for w in app.number_input if w.label=='Days').set_value(10)
    next(w for w in app.button if w.label=='Apply to draft').click().run()
    assert not app.exception
    state=app.session_state['_positions_None:ffa-positions:e3b0c44298fc']
    assert len(state['draft']['trades'])==1 and not state['base']['trades']
    next(w for w in app.get('segmented_control') if w.label=='Position view').set_value('Positions').run()
    assert not app.exception and app.dataframe
    assert app.get('download_button')
    next(w for w in app.button if w.label=='Refresh').click().run()
    assert not app.exception
    state=app.session_state['_positions_None:ffa-positions:e3b0c44298fc']
    assert len(state['draft']['trades'])==1
