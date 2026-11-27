"""P&L presentation from one account valuation; never loads quotes or replays costs."""
import hashlib
import html
import pandas as pd
import streamlit as st
from plotly.subplots import make_subplots
from .positions import EPS,ordered
from .quote_views import UP,DOWN,FLAT,bars
from .ui import chart,date_axis,display


def contract_histories(detail):
    """First contract valuation starts from zero; unknown prior totals stay unknown."""
    paths={}
    if detail.empty:return paths
    for identity,rows in detail.groupby('Contract ID',sort=False):
        path=rows.sort_values('Date').copy()
        path['Daily P&L']=path['Total P&L'].diff()
        path.loc[path.index[0],'Daily P&L']=path['Total P&L'].iloc[0]
        paths[identity]=path
    return paths


def position_events(trades):
    """Quantity transitions identify markers only; all monetary values come from detail."""
    nets={};seen=set();events={}
    for trade in ordered(trades):
        identity=trade['contract'];before=nets.get(identity,0.0)
        after=before+float(trade['days'])*(1 if trade['side']=='Buy' else -1)
        event=None
        if abs(before)<EPS and abs(after)>EPS:event='Reopen' if identity in seen else 'Open'
        elif abs(after)<EPS and abs(before)>EPS:event='Close'
        elif before*after<0:event='Reverse'
        nets[identity]=after;seen.add(identity)
        if event:
            events.setdefault(identity,[]).append({'Date':pd.Timestamp(trade['date']),'Event':event,'Net':after,
                'Description':f'{event}: {trade["side"]} {trade["days"]:g} days @ {trade["price"]:,.2f}'})
    return {identity:pd.DataFrame(rows) for identity,rows in events.items()}


def contract_events(path,events):
    events=events[events.Date.between(path.Date.min(),path.Date.max())].copy()
    settled=path[path.Status.eq('Settled') & ~path.Status.shift().eq('Settled')]
    for _,row in settled.iterrows():
        prior=events[events.Date.le(row.Date)] if len(events) else events
        if len(prior) and abs(prior.iloc[-1].Net)>EPS:
            events=pd.concat([events,pd.DataFrame([{'Date':row.Date,'Event':'Settlement','Net':0.0,'Description':'Final settlement'}])],ignore_index=True)
    return events.sort_values('Date',kind='stable') if len(events) else events


def combination_chart(path,events):
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[.70,.30],vertical_spacing=.07)
    fig.update_layout(chart('P&L · USD',height=420).layout)
    for column,name,color,dash in [('Total P&L','Total',UP,'solid'),('Realized P&L','Realized','#3a59a6','dash'),('Unrealized P&L','Unrealized','#b67132','dot')]:
        fig.add_scatter(x=path.Date,y=path[column],name=name,mode='lines+markers',marker_size=3,
                        line=dict(color=color,width=2,dash=dash),connectgaps=False,
                        hovertemplate=f'%{{x|%d %b %Y}}<br>{name}: $%{{y:+,.2f}}<extra></extra>',row=1,col=1)
    fig.add_bar(x=path.Date,y=path['Daily P&L'],name='Daily',marker_color=[UP if v>0 else DOWN if v<0 else FLAT for v in path['Daily P&L']],
                hovertemplate='%{x|%d %b %Y}<br>Daily: $%{y:+,.2f}<extra></extra>',row=2,col=1)
    if len(events):
        markers=events.groupby('Date',sort=True).agg(Event=('Event',lambda s:' / '.join(s)),Description=('Description',lambda s:'<br>'.join(html.escape(x) for x in s))).reset_index()
        total=path.set_index('Date')['Total P&L']
        fig.add_scatter(x=markers.Date,y=markers.Date.map(total),mode='markers',name='Position events',
                        marker=dict(size=8,symbol='diamond-open',color='#5a6879'),customdata=markers[['Event','Description']],
                        hovertemplate='%{x|%d %b %Y}<br>%{customdata[1]}<br>End-of-day Total: $%{y:+,.2f}<extra></extra>',row=1,col=1)
    date_axis(fig,(path.Date.min(),path.Date.max()))
    fig.update_xaxes(rangeslider_visible=False,row=1,col=1)
    fig.update_xaxes(rangeslider_visible=True,row=2,col=1)
    fig.update_yaxes(title_text='P&L · USD',row=1,col=1)
    fig.update_yaxes(title_text='Daily · USD',tickformat='+,.0f',gridcolor='#e7edf3',row=2,col=1)
    fig.add_hline(y=0,line_width=1,line_color='#aebfcd',row=1,col=1)
    fig.add_hline(y=0,line_width=1,line_color='#aebfcd',row=2,col=1)
    return fig


def contract_header(row):
    def money(value,signed=False):
        return ('$'+format(value,'+,.2f' if signed else ',.2f')) if pd.notna(value) else 'Unavailable'
    def item(label,value,color=None):
        style=f'color:{color};' if color else ''
        return f'<span><span style="color:#718292;font-size:.78rem">{label}</span><br><strong style="{style}">{html.escape(value)}</strong></span>'
    net=row['Net Position'];side='Long' if net>EPS else 'Short' if net<-EPS else 'Closed'
    st.markdown(f'**{html.escape(str(row.Vessel))} {html.escape(str(row.Contract))}** · {html.escape(str(row.Benchmark))}')
    fields=[item('Position',side),item('Net Position',f'{net:+g} days'),item('Average Cost',money(row['Average Cost']) if abs(net)>EPS else '—'),item('Latest BFA',money(row.BFA))]
    for label in ('Daily P&L','Realized P&L','Unrealized P&L','Total P&L'):
        value=row[label];color=UP if pd.notna(value) and value>0 else DOWN if pd.notna(value) and value<0 else FLAT
        fields.append(item(label,money(value,True),color))
    st.markdown('<div style="display:flex;flex-wrap:wrap;gap:.5rem 1.3rem;margin:.25rem 0 .45rem">'+''.join(fields)+'</div>',unsafe_allow_html=True)
    mark=f'{row["Quote Date"]:%d %b %Y}' if pd.notna(row['Quote Date']) else 'unavailable'
    st.caption(f'Valuation {row.Date:%d %b %Y} · Quote {mark} · {row.Status}')


def contract_pnl(paths,trades,key):
    events=position_events(trades)
    active=[identity for identity,path in paths.items() if abs(path['Net Position'].iloc[-1])>EPS]
    closed=[identity for identity in paths if identity not in active]
    def render(identity):
        path=paths[identity];row=path.iloc[-1];markers=contract_events(path,events.get(identity,pd.DataFrame(columns=['Date','Event','Net','Description'])))
        contract_header(row)
        # A closed chart ends at the final close/settlement, avoiding years of
        # a flat post-close tail. Reopened contracts always use the entire path.
        if abs(row['Net Position'])<=EPS and len(markers):
            ending=markers[markers.Event.isin(['Close','Settlement'])]
            if len(ending):path=path[path.Date.le(ending.Date.max())]
        chart_key=key+'_contract_'+hashlib.sha256(identity.encode()).hexdigest()[:16]
        display(combination_chart(path,markers),chart_key)
    st.subheader('Contract P&L')
    for identity in active:render(identity)
    if not active:st.caption('No open positions. Closed contract histories remain available below.')
    if closed:
        folder=st.expander(f'Closed Positions ({len(closed)})',expanded=False,key=key+'_closed_pnl',on_change='rerun')
        if folder.open:
            with folder:
                for identity in closed:render(identity)


def account_pnl(history,positions,key):
    st.subheader('Account P&L')
    if history.empty:
        st.caption('Performance appears after the first confirmed transaction.');return
    bounds=(history.Date.min(),history.Date.max())
    fig=chart('USD',height=330);fig.add_scatter(x=history.Date,y=history['Total P&L'],name='Cumulative P&L',line_color=UP,connectgaps=False)
    fig.add_hline(y=0,line_width=1);date_axis(fig,bounds);display(fig,key+'_cumulative')
    fig=chart('USD',height=300);bars(fig,history.set_index('Date').rename(columns={'Daily P&L':'change'}),'Daily P&L');date_axis(fig,bounds);display(fig,key+'_daily')
    rank=positions.sort_values('Total P&L');fig=chart(None,height=max(260,35*len(rank)))
    fig.add_bar(y=[f'{r.Vessel} {r.Contract}' for _,r in rank.iterrows()],x=rank['Total P&L'],orientation='h',marker_color=[UP if v>=0 else DOWN for v in rank['Total P&L']])
    fig.update_xaxes(title='Contract P&L · USD');display(fig,key+'_ranking')
