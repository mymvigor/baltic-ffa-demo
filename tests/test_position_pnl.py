from copy import deepcopy
import numpy as np
import pandas as pd
import pytest
from ffa.analytics import frame
from ffa.positions import calculate
from ffa.position_pnl import contract_histories,position_events,contract_events,combination_chart
from ffa.settlement import sessions
from test_positions import ledger,trade,quotes


def scenario():
    trades=ledger()+[trade(7,'Buy',3,70,1),trade(8,'Sell',1,90,1),trade(9,'Sell',5,80,2),trade(10,'Buy',3,60,1)]
    fixed=quotes()
    extra=[]
    for d,price in [('2026-11-08',90),('2026-11-09',100),('2026-11-10',75),('2026-11-11',60),('2026-11-12',120)]:
        row=fixed.iloc[0].to_dict();row.update(quote_day=d,price=price);extra.append(row)
    return trades,pd.concat([fixed,frame(extra)],ignore_index=True)


def test_reopen_retains_realized_fees_and_first_trade_history():
    trades,fixed=scenario()
    current,history,detail=calculate(trades,fixed,'2026-11-10')
    paths=contract_histories(detail);path=paths['dec'];last=path.iloc[-1]
    assert path.Date.min()==pd.Timestamp('2026-11-02')
    # Prior closed cycle -140; reopen fees 4, partial closes +40 => -104 realized.
    assert last['Realized P&L']==-104 and last['Unrealized P&L']==15 and last['Total P&L']==-89
    assert last['Daily P&L']==-27 and last['Net Position']==-3 and last['Average Cost']==80
    events=contract_events(path,position_events(trades)['dec'])
    assert list(events.Event)==['Open','Reverse','Close','Reopen','Reverse']
    assert events.Date.max()<=path.Date.max() # future closes are not plotted
    current,history,detail=calculate(trades,fixed,'2026-11-12')
    path=contract_histories(detail)['dec'];last=path.iloc[-1]
    assert last['Net Position']==0 and last['Unrealized P&L']==0
    assert last['Realized P&L']==-45 and last['Total P&L']==-45 and last['Daily P&L']==0


def test_contract_curve_and_daily_sums_equal_account_including_closed_positions():
    trades,fixed=scenario()
    for i,side,price,fee in [(4,'Buy',50,3),(8,'Sell',70,1)]:
        t=trade(i,side,2,price,fee,'other');t['id']='other-'+t['id'];t['product']='S11TC';t['vessel']='SMX';trades.append(t)
    rows=[]
    for day,price in [('2026-11-05',60),('2026-11-06',65),('2026-11-07',68),('2026-11-08',65),('2026-11-09',70)]:
        row=fixed.iloc[0].to_dict();row.update(identity='other',vessel='SMX',product='S11TC',quote_day=day,price=price);rows.append(row)
    fixed=pd.concat([fixed,frame(rows)],ignore_index=True)
    current,history,detail=calculate(trades,fixed,'2026-11-12')
    paths=contract_histories(detail)
    assert paths['other']['Realized P&L'].iloc[-1]==36
    assert history['Total P&L'].iloc[-1]==-9 # -45 + 36
    combined=pd.concat(paths.values(),ignore_index=True)
    for column in ['Realized P&L','Unrealized P&L','Total P&L','Daily P&L']:
        expected=history.set_index('Date')[column]
        summed=combined.groupby('Date')[column].sum(min_count=1)
        pd.testing.assert_series_equal(summed.reindex(expected.index),expected,check_names=False)
    np.testing.assert_allclose(detail['Total P&L'],detail['Realized P&L']+detail['Unrealized P&L'])


def test_missing_prior_valuation_is_not_zero_daily_pnl():
    t=trade(1,'Buy',1,100);fixed=quotes().iloc[1:3]
    current,history,detail=calculate([t],fixed,'2026-11-04')
    path=contract_histories(detail)['dec']
    assert pd.isna(path['Daily P&L'].iloc[0]) and pd.isna(path['Daily P&L'].iloc[1])
    assert path['Daily P&L'].iloc[2]==20


def test_verified_settlement_keeps_realized_and_marks_final_date():
    t={**trade(1,'Buy',5,100,2),'date':'2026-01-02','delivery_start':'2026-01-01','delivery_end':'2026-01-31'}
    raw=[dict(identity='spot',quote_day=d,vessel='PMX',product='P5TC',currency='USD',unit='day',kind='Spot',tenor='spot',price=110) for d in sessions('2026-01')]
    raw.append(dict(identity='dec',quote_day='2026-01-02',vessel='PMX',product='P5TC',currency='USD',unit='day',kind='BFA',tenor='month',delivery_start='2026-01-01',delivery_end='2026-01-31',price=105))
    current,history,detail=calculate([t],frame(raw),'2026-02-02')
    path=contract_histories(detail)['dec'];last=path.iloc[-1]
    assert last['Realized P&L']==48 and last['Unrealized P&L']==0 and last['Total P&L']==48
    events=contract_events(path,position_events([t])['dec'])
    assert list(events.Event)==['Open','Settlement'] and events.Date.iloc[-1]==pd.Timestamp('2026-01-30')


def test_combined_chart_has_three_curves_daily_bars_and_one_shared_slider():
    trades,fixed=scenario();_,_,detail=calculate(trades,fixed,'2026-11-10')
    path=contract_histories(detail)['dec'];events=contract_events(path,position_events(trades)['dec'])
    fig=combination_chart(path,events)
    assert [t.name for t in fig.data]==['Total','Realized','Unrealized','Daily','Position events']
    assert all(t.yaxis=='y' for t in fig.data[:3]) and fig.data[3].yaxis=='y2'
    assert fig.layout.xaxis.matches=='x2'
    assert not fig.layout.xaxis.rangeslider.visible and fig.layout.xaxis2.rangeslider.visible
    assert list(fig.data[0].y)==list(path['Total P&L']) and list(fig.data[3].y)==list(path['Daily P&L'])
