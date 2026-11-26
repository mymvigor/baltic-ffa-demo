"""Excel import validation and reports generated from the same ledger calculations."""
from io import BytesIO
import re
import uuid
import pandas as pd
from .positions import calculate, validate_trade
from .selection import contract_label

ALIASES={'date':['trade date','date','交易日期','成交日期'], 'contract':['contract','合约'],
         'side':['buy/sell','side','direction','买卖','方向'], 'price':['price','trade price','成交价','价格'],
         'days':['days','quantity','天数','数量'], 'fees':['fees','fee','交易费用','手续费'],
         'notes':['notes','note','备注'], 'trader':['trader','trader name','交易员'],
         'vessel':['vessel','vessel class','船型'], 'product':['benchmark','product','指数']}


def infer_columns(table):
    names={str(c).strip().casefold():c for c in table.columns}
    return {k:next((names[a] for a in aliases if a in names),None) for k,aliases in ALIASES.items()}


def fingerprint(t):
    return (t['date'],t['contract'],t['side'],round(float(t['price']),8),round(float(t['days']),8),round(float(t.get('fees',0)),8))


def metadata(row):
    return {'contract':row.identity,'vessel':row.vessel,'product':row['product'],'currency':row.currency,'unit':row.unit,
            'tenor':row.tenor,'delivery_start':str(pd.Timestamp(row.delivery_start).date()),'delivery_end':str(pd.Timestamp(row.delivery_end).date()),
            'label':contract_label(row)}


def preview(table, columns, catalog, existing, trader=None, default_vessel=None, default_product=None, allow_duplicates=False):
    if any(columns.get(k) is None for k in ('date','contract','side','price','days')):return [],['Map Trade Date, Contract, Buy/Sell, Price and Days.'],0
    accepted=[];errors=[];duplicates=0;seen={fingerprint(t) for t in existing}
    normalize=lambda x:re.sub(r'[^A-Z0-9]','',str(x).upper())
    for number,(_,row) in enumerate(table.iterrows(),start=2):
        if trader is not None and columns.get('trader') and str(row[columns['trader']])!=str(trader):continue
        try:
            get=lambda k,default=None:row[columns[k]] if columns.get(k) and pd.notna(row[columns[k]]) else default
            rawdate=get('date')
            if isinstance(rawdate,str) and re.match(r'^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$',rawdate.strip()):
                raise ValueError('Use an Excel date cell or YYYY-MM-DD to avoid ambiguous dates.')
            date=pd.Timestamp(rawdate)
            if pd.isna(date) or isinstance(rawdate,(int,float)):raise ValueError('Invalid trade date; use an Excel date cell.')
            code=normalize(get('contract'));ship=get('vessel',default_vessel);product=get('product',default_product)
            pool=catalog
            if ship:
                aliases={'PANAMAX':'PMX','CAPESIZE':'CAPE','SUPRAMAX':'SMX','HANDYSIZE':'HDY'}
                ship=aliases.get(str(ship).upper(),str(ship).upper());pool=pool[pool.vessel.eq(ship)]
            if product:pool=pool[pool['product'].str.casefold().eq(str(product).casefold())]
            match=pool[pool.apply(lambda r:code in (normalize(contract_label(r).split(' · ')[0]),normalize(r.identity)),axis=1)]
            if len(match)!=1:raise ValueError('Contract is missing or ambiguous; specify vessel and benchmark.')
            side=str(get('side')).strip().casefold();side={'buy':'Buy','b':'Buy','买':'Buy','买入':'Buy','sell':'Sell','s':'Sell','卖':'Sell','卖出':'Sell'}.get(side)
            numeric=lambda k,default=0:float(str(get(k,default)).replace(',','').replace('$',''))
            trade=dict(metadata(match.iloc[0]),id=uuid.uuid4().hex,date=str(date.date()),side=side,price=numeric('price'),days=numeric('days'),
                       fees=numeric('fees'),notes=str(get('notes','')),sequence=max((t.get('sequence',0) for t in existing),default=0)+len(accepted)+1,
                       import_row=number,import_trader=str(trader) if trader is not None else '')
            validate_trade(trade);fp=fingerprint(trade)
            if fp in seen and not allow_duplicates:duplicates+=1;continue
            seen.add(fp);accepted.append(trade)
        except (ValueError,TypeError,KeyError) as exc:errors.append(f'Row {number}: {exc}')
    return accepted,errors,duplicates


def report(trades, fixed, start, end, vessels=None, contracts=None):
    """Replay before filtering, with an explicit opening valuation and predecessor quotes."""
    start,end=pd.Timestamp(start),pd.Timestamp(end)
    selected=[t for t in trades if (not vessels or t['vessel'] in vessels) and (not contracts or t['contract'] in contracts)]
    positions,history,detail=calculate(selected,fixed,end)
    opening,opening_history,_=calculate(selected,fixed,start-pd.Timedelta(days=1))
    opening_total=opening_history['Total P&L'].iloc[-1] if len(opening_history) else 0.0
    period=history[history.Date.between(start,end)].copy() if len(history) else history
    ending=history['Total P&L'].iloc[-1] if len(history) else 0.0
    summary=pd.DataFrame([{'Item':'Period Start','Value':str(start.date())},{'Item':'Period End / Valuation Date','Value':str(end.date())},
                          {'Item':'Opening Valuation Date','Value':str((start-pd.Timedelta(days=1)).date())},
                          {'Item':'Opening Cumulative P&L','Value':opening_total},{'Item':'Closing Cumulative P&L','Value':ending},
                          {'Item':'Period P&L','Value':ending-opening_total}])
    open_rows=opening.assign(Section='Opening Positions');close_rows=positions.assign(Section='Closing Positions')
    daily=pd.concat([summary,open_rows,close_rows],ignore_index=True)
    transaction=pd.DataFrame([dict(t,Scope='Period' if start<=pd.Timestamp(t['date'])<=end else 'Opening history')
                              for t in selected if pd.Timestamp(t['date'])<=end])
    ids={t['contract'] for t in selected}
    quotes=fixed[fixed.kind.eq('BFA') & fixed.identity.isin(ids) & fixed.quote_day.le(end)].sort_values(['identity','quote_day'])
    pre=quotes[quotes.quote_day.lt(start)].groupby('identity').tail(6)
    quotes=pd.concat([pre,quotes[quotes.quote_day.ge(start)]]).drop_duplicates(['identity','quote_day'])
    buffer=BytesIO()
    with pd.ExcelWriter(buffer,engine='openpyxl') as writer:
        for name,table in [('Daily Report',daily),('P&L History',period),('Transaction History',transaction),('BFA Prices',quotes)]:
            table=table.copy()
            for column in table:
                if isinstance(table[column].dtype,pd.DatetimeTZDtype):
                    # Preserve publication timestamps and their zone as ISO text.
                    table[column]=table[column].map(lambda v:v.isoformat() if pd.notna(v) else None)
            table.to_excel(writer,sheet_name=name,index=False)
            sheet=writer.sheets[name];sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
            for col in sheet.columns:
                sheet.column_dimensions[col[0].column_letter].width=min(48,max(13,max(len(str(c.value or '')) for c in col)+2))
                for cell in col:
                    if isinstance(cell.value,str) and cell.value.startswith(('=','+','-','@')):cell.data_type='s'
    return buffer.getvalue()
