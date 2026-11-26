from copy import deepcopy
import base64,json
import pytest
from ffa.positions import empty_book,calculate
from ffa.position_sync import contract_catalog,build_batch,sync_batch,raw_trade,validate_changes
from ffa.position_store import Conflict,load
from test_positions import quotes,trade,ledger


class Response:
    def __init__(self,status,value):self.status_code=status;self.value=value
    def json(self):return self.value


class PrivateStore:
    def __init__(self,book=None):self.book=deepcopy(book or empty_book());self.sha='0';self.puts=0;self.fail=None
    def request(self,method,path='',**kw):
        if path.startswith('/git/ref/'):return Response(200,{})
        if method=='GET':return Response(200,{'sha':self.sha,'encoding':'base64','content':base64.b64encode(json.dumps(self.book).encode()).decode()})
        assert method=='PUT' and kw['json']['branch']=='ffa-positions'
        self.puts+=1
        if self.fail=='before':return Response(500,{})
        assert kw['json'].get('sha')==self.sha
        self.book=json.loads(base64.b64decode(kw['json']['content']));self.sha=str(int(self.sha)+1)
        if self.fail=='after':self.fail=None;raise ConnectionError('response lost')
        return Response(200,{'content':{'sha':self.sha}})


def event(catalog,id='new1',base=None,**fields):
    raw={'Trade Date':'2026-11-02','Contract':next(iter(catalog.values()))['search'],'Side':'Buy','Price':100,'Quantity':5,'Fees':0,'Notes':''}
    raw.update(fields)
    return {id:{'base':base,'fields':raw,'order':1}}


def test_complete_batch_one_commit_incomplete_batch_no_writes():
    fixed=quotes();catalog=contract_catalog(fixed);storage=PrivateStore()
    payload={'edits':event(catalog),'deletes':{}}
    payload['edits'].update(event(catalog,'new2'))
    saved,sha,batch=sync_batch(storage,payload,catalog,fixed,'2026-11-07','ffa-positions')
    assert storage.puts==1 and len(saved['trades'])==2 # identical legitimate fills stay distinct
    assert saved['trades'][0]['days']==5 and saved['trades'][0]['multiplier']==1
    bad={'edits':event(catalog,'bad',Price=''),'deletes':{}}
    with pytest.raises(ValueError):sync_batch(storage,bad,catalog,fixed,'2026-11-07','ffa-positions')
    assert storage.puts==1 and len(storage.book['trades'])==2


def test_failures_lost_responses_and_redeployment_do_not_duplicate():
    fixed=quotes();catalog=contract_catalog(fixed);storage=PrivateStore();payload={'edits':event(catalog),'deletes':{}}
    storage.fail='before'
    with pytest.raises(RuntimeError):sync_batch(storage,payload,catalog,fixed,'2026-11-07','ffa-positions')
    assert not storage.book['trades']
    storage.fail='after'
    with pytest.raises(ConnectionError):sync_batch(storage,payload,catalog,fixed,'2026-11-07','ffa-positions')
    assert len(storage.book['trades'])==1
    before=storage.puts
    saved,sha,batch=sync_batch(storage,payload,catalog,fixed,'2026-11-07','ffa-positions')
    assert storage.puts==before and len(saved['trades'])==1
    # A fresh app session reads only the authoritative remote ledger.
    reloaded,sha=load(storage);assert reloaded==saved


def test_history_edit_delete_and_same_record_conflict():
    fixed=quotes();book=empty_book();book['trades']=ledger();catalog=contract_catalog(fixed,book['trades']);storage=PrivateStore(book)
    base=book['trades'][0];raw=raw_trade(base,catalog);raw['Price']=90
    payload={'edits':{base['id']:{'base':base,'fields':raw,'order':1}},'deletes':{}}
    saved,_,_=sync_batch(storage,payload,catalog,fixed,'2026-11-07','ffa-positions')
    positions,_,_=calculate(saved['trades'],fixed,'2026-11-07');assert positions['Total P&L'].iloc[0]==pytest.approx(-40)
    modified=deepcopy(saved);modified['trades'][0]['price']=88
    with pytest.raises(Conflict):build_batch(modified,payload,catalog,'2026-11-07')
    delete={'edits':{},'deletes':{t['id']:t for t in saved['trades']}}
    saved,_,_=sync_batch(storage,delete,catalog,fixed,'2026-11-07','ffa-positions')
    assert not saved['trades']


def test_sequential_risk_never_uses_batch_netting_to_allow_reversal():
    fixed=quotes();before=ledger()[:4]
    with pytest.raises(ValueError):validate_changes(before,before+[trade(5,'Buy',10,80),trade(6,'Sell',10,80)],fixed,'2026-11-07',100)
    validate_changes(before,before+[trade(5,'Buy',2,80)],fixed,'2026-11-06',100)


def test_multiplier_does_not_multiply_days_by_delivery_month():
    fixed=quotes();catalog=contract_catalog(fixed)
    candidate=build_batch(empty_book(),{'edits':event(catalog),'deletes':{}},catalog,'2026-11-07')
    t=candidate['trades'][0]
    assert t['days']==5 and t['quantity_unit']=='days' and t['multiplier']==1
    positions,_,_=calculate(candidate['trades'],fixed,'2026-11-07')
    assert positions['Unrealized P&L'].iloc[0]==-100 # 5 * (80 - 100), not 31 times this value


def test_concurrent_new_fills_get_unambiguous_persisted_execution_order():
    fixed=quotes();catalog=contract_catalog(fixed)
    class Concurrent(PrivateStore):
        def request(self,method,path='',**kw):
            if method=='PUT' and self.puts==0:
                self.puts+=1
                other=trade(1,'Buy',2,100);other['id']='other';self.book['trades']=[other];self.sha='1'
                return Response(409,{})
            return super().request(method,path,**kw)
    storage=Concurrent()
    saved,_,_=sync_batch(storage,{'edits':event(catalog),'deletes':{}},catalog,fixed,'2026-11-07','ffa-positions')
    assert [t['id'] for t in saved['trades']]==['other','new1']
    assert [t['sequence'] for t in saved['trades']]==[1,2] and storage.puts==2
