from copy import deepcopy
from io import BytesIO
import base64
import json
import pandas as pd
import numpy as np
import pytest
from ffa.analytics import frame
from ffa.positions import calculate,permits,risk_state,empty_book
from ffa.position_store import merge,Conflict,save
from ffa.position_excel import preview,infer_columns,report
from ffa.quote_views import bfa_table,changes,UP,DOWN
from ffa.settlement import sessions


def trade(i,side,days,price,fees=0,contract='dec'):
    return dict(id=str(i),date=f'2026-11-{i+1:02}',sequence=i,contract=contract,vessel='PMX',product='P5TC',currency='USD',unit='day',
                tenor='month',delivery_start='2026-12-01',delivery_end='2026-12-31',label='DEC26',side=side,days=days,price=price,fees=fees)


def quotes():
    return frame([dict(identity='dec',quote_day=f'2026-11-{i+1:02}',vessel='PMX',product='P5TC',kind='BFA',currency='USD',unit='day',
                       tenor='month',delivery_start='2026-12-01',delivery_end='2026-12-31',price=p) for i,p in enumerate([100,120,140,80,80,80],1)])


def ledger():return [trade(1,'Buy',10,100,5),trade(2,'Buy',10,120),trade(3,'Sell',5,130,2),trade(4,'Sell',20,90,3),trade(5,'Buy',2,70),trade(6,'Buy',3,80)]


def test_hand_calculated_long_partial_short_reversal_and_daily():
    rows=quotes();p,h,d=calculate(ledger(),rows,'2026-11-07')
    # Independently hand-worked: cost 110, partial close +100, reverse -300,
    # short closes +40 and +30, all fees 10 => final realized -140.
    assert list(h['Total P&L'])==pytest.approx([-5,195,543,-160,-140,-140])
    assert h['Daily P&L'].sum()==pytest.approx(-140)
    assert p['Net Position'].iloc[0]==0 and p['Realized P&L'].iloc[0]==pytest.approx(-140)
    reverse=d[d.Date.eq('2026-11-05')].iloc[0]
    assert (reverse['Net Position'],reverse['Average Cost'],reverse['Unrealized P&L'])==(-5,90,50)
    revised=deepcopy(ledger());revised[0]['price']=90
    p2,_,_=calculate(revised,rows,'2026-11-07')
    assert p2['Total P&L'].iloc[0]==pytest.approx(-40)


def test_risk_whole_account_no_reversal_or_cross_contract_bypass():
    old=ledger()[:4];rows=quotes();p,_,_=calculate(old,rows,'2026-11-05')
    assert risk_state(p,100)[0]=='Red'
    assert permits(old,old+[trade(5,'Buy',2,80)],rows,'2026-11-06',100)[0]
    assert not permits(old,old+[trade(5,'Buy',6,80)],rows,'2026-11-06',100)[0]
    assert not permits(old,old+[trade(5,'Buy',10,80),trade(6,'Sell',10,80)],rows,'2026-11-07',100)[0]
    other=trade(5,'Buy',1,80,contract='other')
    assert not permits(old,old+[other],rows,'2026-11-06',100)[0]
    costly=trade(1,'Buy',1,100,fees=200)
    assert not permits([], [costly],rows,'2026-11-02',100)[0]
    missing=frame([]);p,_,_=calculate([trade(1,'Buy',10,100)],missing,'2026-11-03')
    assert np.isnan(p['Total P&L'].iloc[0]) and risk_state(p,25000)[0]=='Unknown'
    assert permits([trade(1,'Buy',10,100)],[trade(1,'Buy',10,100),trade(2,'Sell',10,110)],missing,'2026-11-03',25000)[0]


def test_quote_dates_pending_and_verified_settlement():
    p,h,_=calculate([trade(1,'Buy',10,100)],quotes(),'2026-11-08')
    assert p['Quote Date'].iloc[0]==pd.Timestamp('2026-11-07')
    assert p.Status.iloc[0]=='Prior quote' and h['Daily P&L'].iloc[-1]==0
    p,_,_=calculate([trade(1,'Buy',10,100)],quotes(),'2027-01-02')
    assert p.Status.iloc[0]=='Settlement pending' and np.isnan(p['Total P&L'].iloc[0])
    t=dict(trade(1,'Buy',10,100),date='2026-01-02',delivery_start='2026-01-01',delivery_end='2026-01-31')
    raw=[dict(identity='spot',quote_day=d,vessel='PMX',product='P5TC',currency='USD',unit='day',kind='Spot',tenor='spot',price=110) for d in sessions('2026-01')]
    p,h,_=calculate([t],frame(raw),'2026-02-02')
    assert p.Status.iloc[0]=='Settled' and p['Net Position'].iloc[0]==0 and p['Realized P&L'].iloc[0]==100
    p,_,_=calculate([t],frame(raw[:-1]),'2026-02-02')
    assert p.Status.iloc[0]=='Settlement pending' and np.isnan(p['Total P&L'].iloc[0])


def test_excel_import_duplicates_trader_and_export_opening_cost():
    rows=quotes();catalog=rows.drop_duplicates('identity')
    raw=pd.DataFrame({'Trade Date':[pd.Timestamp('2026-11-02')]*3,'Contract':['DEC26']*3,'Buy/Sell':['Buy']*3,'Price':[100]*3,'Days':[10]*3,'Trader':['A','A','B']})
    accepted,errors,duplicates=preview(raw,infer_columns(raw),catalog,[],trader='A',default_vessel='PMX',default_product='P5TC')
    assert len(accepted)==1 and not errors and duplicates==1
    repeated,errors,duplicates=preview(raw,infer_columns(raw),catalog,[],trader='A',default_vessel='PMX',default_product='P5TC',allow_duplicates=True)
    assert len(repeated)==2 and not errors and duplicates==0
    blob=report(ledger(),rows,'2026-11-05','2026-11-07')
    x=pd.ExcelFile(BytesIO(blob));assert x.sheet_names==['Daily Report','P&L History','Transaction History','BFA Prices']
    daily=x.parse('Daily Report');summary=daily.dropna(subset='Item').set_index('Item').Value
    assert summary['Opening Cumulative P&L']==543 and summary['Closing Cumulative P&L']==-140
    assert summary['Period P&L']==-683
    assert len(x.parse('Transaction History'))==6 # includes cost history before the date filter


def test_private_store_conflict_merge_and_no_silent_overwrite():
    base=empty_book();local=deepcopy(base);remote=deepcopy(base)
    local['trades']=[trade(1,'Buy',1,100)];remote['trades']=[trade(2,'Sell',1,110)]
    assert len(merge(base,local,remote)['trades'])==2
    base=deepcopy(local);a=deepcopy(base);b=deepcopy(base);a['trades'][0]['price']=101;b['trades'][0]['price']=102
    with pytest.raises(Conflict):merge(base,a,b)
    class Response:
        def __init__(self,code,value):self.status_code=code;self.value=value
        def json(self):return self.value
    class Store:
        def __init__(self):self.value=empty_book();self.sha='a';self.puts=0
        def request(self,method,path='',**kwargs):
            if path.startswith('/git/ref/'):return Response(200,{})
            if method=='GET':return Response(200,{'sha':self.sha,'encoding':'base64','content':base64.b64encode(json.dumps(self.value).encode()).decode()})
            self.puts+=1
            if self.puts==1:
                self.value=remote;self.sha='b';return Response(409,{})
            assert kwargs['json']['sha']=='b'
            self.value=json.loads(base64.b64decode(kwargs['json']['content']));return Response(200,{'content':{'sha':'c'}})
    store=Store();saved,sha=save(store,empty_book(),local)
    assert store.puts==2 and len(saved['trades'])==2 and sha=='c'


def test_recent_quotes_predecessors_missing_and_roll_change():
    q=quotes();data,styles=bfa_table(q,'2026-11-07',5,['dec'])
    assert data.Change.iloc[0]==0 and data['1D %'].iloc[0]==0 and data['5D %'].iloc[0]==pytest.approx(-20)
    assert UP in styles['04 Nov 2026'].iloc[0] and DOWN in styles['05 Nov 2026'].iloc[0]
    raw=pd.DataFrame({'price':[100,np.nan,110,200,190],'segment':[1,1,1,2,2]})
    c=changes(raw);assert c.change.iloc[1]==10 and np.isnan(c.change.iloc[2]) and c.change.iloc[3]==-10


def test_export_preserves_publication_timezone_and_does_not_execute_notes():
    rows=quotes();rows['available_at']=pd.to_datetime(rows.quote_day,utc=True)
    t=trade(1,'Buy',1,100);t['notes']='=1+1'
    from openpyxl import load_workbook
    workbook=load_workbook(BytesIO(report([t],rows,'2026-11-02','2026-11-07')))
    sheet=workbook['Transaction History'];headers=[c.value for c in sheet[1]]
    assert sheet.cell(2,headers.index('notes')+1).data_type=='s'
    sheet=workbook['BFA Prices'];headers=[c.value for c in sheet[1]]
    assert sheet.cell(2,headers.index('available_at')+1).value.endswith('+00:00')
