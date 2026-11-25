"""Shared Spot/BFA forecasting and parameter-linked replay. Reuses the journal."""
from hashlib import sha256
import json
import numpy as np
import pandas as pd
import streamlit as st
from .analytics import delivery_label
from .outlook import DEFAULTS, forecast_path, apply_judgement, prediction_record, replay_current, forecast_score, evaluated_record
from .outlook_ui import _memory, _records, _future_chart
from .forecast_store import save_records, activate_configuration
from .preferences import choice, multi, calendar, encode
from .settlement import sessions
from .settlement_ui import _trajectory_axis
from .store import connect
from .ui import chart, display, COLORS


@st.cache_data(max_entries=16,show_spinner=False)
def _projection(stamp,contract,config,_fixed):
    return forecast_path(_fixed,contract,json.loads(config))


@st.cache_data(max_entries=12,show_spinner=False)
def _replay(stamp,contract,config,horizon,strict,_fixed):
    return replay_current(_fixed,contract,json.loads(config),horizon,strict)


def unified_forecast(fixed,stamp,vessel,product,key,kind,synthetic=False,repo=None,credential_id=None,token=None):
    today=pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    fixed=fixed[fixed.vessel.eq(vessel)&fixed["product"].eq(product)&fixed.currency.eq("USD")&fixed.unit.eq("day")&fixed.quote_day.le(today)]
    current=fixed[fixed.kind.eq(kind)&fixed.price.notna()].sort_values("quote_day")
    if current.empty:
        st.info("No compatible quotes available.");return
    latest=current.quote_day.max()
    if kind=="BFA":
        contracts=current[current.delivery_end.gt(today)].drop_duplicates("identity",keep="last").sort_values("delivery_start")
        if contracts.empty:
            st.info("No active fixed contracts.");return
        labels={r.identity:f"{delivery_label(r.tenor,r.delivery_start)} · {r.tenor}" for r in contracts.itertuples()}
        contract=choice("Target contract",labels,key+"_contract",format_func=labels.get)
        expiry=contracts.set_index("identity").loc[contract].delivery_end
        current=current[current.identity.eq(contract)]
        latest=current.quote_day.max()
    else:
        contract=f"spot:{vessel}:{product}"
        expiry=today+pd.DateOffset(years=2)
    state=key+sha256(contract.encode()).hexdigest()[:8]
    model=choice("Model",["Difference","Ratio"],state+"_method",control="segmented_control")
    window=choice("Reference Window",[5,10,20,30,60,"Custom"],state+"_window",20)
    config={**DEFAULTS,"model":model,"target_kind":kind}
    if window=="Custom":
        a=calendar("Reference from",state+"_from",max(current.quote_day.min().date(),(latest-pd.Timedelta(days=28)).date()),current.quote_day.min().date(),latest.date())
        b=calendar("Reference to",state+"_to",latest.date(),a,latest.date())
        config.update(custom=[str(a),str(b)],window=max(1,int(current.quote_day.between(pd.Timestamp(a),pd.Timestamp(b)).sum())))
    else:
        config["window"]=window
    end=calendar("Forecast through",state+"_end",(today+pd.DateOffset(months=3)).date() if kind=="Spot" else expiry.date(),today.date(),expiry.date())
    config["forecast_end"]=str(end)
    with st.popover("Advanced Settings"):
        config["weighting"]=choice("Reference weighting",["Equal weighted","Recent weighted"],state+"_weight")
        if kind=="BFA":
            config["pooled"]=choice("Validation sample",["Historical peers","Selected contract"],state+"_sample")=="Historical peers"
        partition=choice("Evaluation partition",["Holdout","Development"],state+"_partition")
        timing=choice("Historical data",["Historical Replay","Verified timestamps"],state+"_timing")
        st.caption("Historical Replay cannot verify archived publication times or revisions. Strict mode rejects undated observations. Repeated parameter exploration is not an untouched final test.")
    stamp=(stamp,len(fixed),kind)
    basic_json=json.dumps(config,sort_keys=True)
    path,info=_projection(stamp,contract,basic_json,fixed)
    if path.empty:
        st.info(info.get("reason","Insufficient history."));return
    years=multi("Reference Years",info["candidate_years"],state+"_years",info["reference_years"],len(info["candidate_years"]))
    if not years:
        st.info("Select a historical year.");return
    config["years"]=list(years)
    basic_json=json.dumps(config,sort_keys=True)
    path,info=_projection(stamp,contract,basic_json,fixed)
    if path.empty:
        st.info(info.get("reason","No common coverage."));return
    path=path.copy()
    exploratory=choice("Forecast model",["Basic Models","Exploratory Model"],state+"_explore",control="segmented_control")=="Exploratory Model"
    if exploratory:
        config["judgement"]=choice("Market judgement",["Neutral","Bearish","Bullish"],state+"_judgement",control="segmented_control")
        config["strength"]=st.number_input("Judgement strength",0.,1.,.3,.1,key=state+"_strength")
        with st.popover("Scenario settings"):
            config["maximum_pct"]=st.number_input("Maximum adjustment (%)",0.,100.,10.,1.,key=state+"_max")
            config["reason"]=st.text_input("Reason",key=state+"_reason")
            default=min(end,(today+pd.offsets.BDay(20)).date())
            deadline=calendar("Judgement ends",state+"_deadline",default,today.date(),end)
            config.update(judgement_from=str(today.date()),judgement_until=str(deadline),fade=True)
        st.caption("Subjective adjustments change the future scenario only. They are excluded from historical scores; saved forecasts provide prospective evaluation.")
    path["scenario"]=apply_judgement(path,config)
    view=choice("Chart view",["Future Forecast","Forecast vs Actual","Forecast Error","Forecast Evolution"],state+"_view",control="segmented_control")
    mode=choice("Review data",["Historical Replay","Saved Forecasts"],state+"_review_mode",control="segmented_control") if view!="Future Forecast" else "Historical Replay"
    saved_mode=mode=="Saved Forecasts"
    horizon=choice("Evaluation Horizon",[1,5,10,20,60],state+"_horizon",20,control="segmented_control",format_func=lambda n:f"{n}D")
    replay=pd.DataFrame()
    if not saved_mode:
        with st.spinner("Updating validation for the current parameters…"):
            replay=_replay(stamp,contract,basic_json,horizon,timing=="Verified timestamps",fixed)
        if not replay.empty:
            cutoff=sorted(replay.date.unique())[int(replay.date.nunique()*.7)]
            replay=replay[replay.date.ge(cutoff)] if partition=="Holdout" else replay[replay.target_date.lt(cutoff)]
        score=forecast_score(replay)
        cols=st.columns(4)
        cols[0].metric("Direction Accuracy",f"{score['direction']:.1%}" if np.isfinite(score['direction']) else "—")
        cols[1].metric("Forecast Skill Score",f"{score['skill']:+.1f}%" if np.isfinite(score['skill']) else "—")
        cols[2].metric("MAE",f"{score['mae']:,.0f}" if np.isfinite(score['mae']) else "—")
        cols[3].metric("Samples",score['samples'])
        if score['samples']<10:
            st.caption("Fewer than 10 non-overlapping cases. No reliable score or ranking is reported.")
        else:
            st.caption("Historical error beats unchanged price." if score['skill']>0 else "This sample does not establish a price advantage over unchanged price.")
            with st.popover("Score details"):
                st.caption(f"Out-of-sample R² {score['r2']:.2f}; Bias {score['bias']:+,.0f}; momentum MAE {score['momentum_mae']:,.0f}; past-majority direction accuracy {score['majority_accuracy']:.1%}. Flat is a separate direction. Scores describe exploration, not trading returns.")
    formula="C(t) + A − B" if model=="Difference" else "C(t) × A / B"
    st.caption(f"{formula} · A {info['level']:,.0f} · B {info['historical_reference']:,.0f}. The same historical-year cohort is required at every point; absent matches remain gaps.")
    record=prediction_record(path,info,contract,config,info["source_revision"])
    if record and not synthetic:
        record=_memory(key).setdefault(record["id"],encode(record))
    if view=="Future Forecast":
        future=path[path.target_date.gt(today)]
        if not future.empty:
            actual=current[current.identity.eq(contract)] if kind=="BFA" else current
            _future_chart(actual,future,info,"Price path","Exploratory scenario" if exploratory else "Basic",False,[],state)
    else:
        saved=_records(key,contract,repo,credential_id,token) if mode=="Saved Forecasts" else None
        if view=="Forecast Evolution" and mode=="Historical Replay":
            target=calendar("Evolution target date",state+"_target_day",end,current.quote_day.min().date(),end)
            with st.spinner("Replaying forecasts for the selected target…"):
                replay=_evolution(stamp,contract,basic_json,str(target),timing=="Verified timestamps",fixed)
        _review_chart(fixed,replay,saved,view,state,config,kind,contract,horizon)
    if kind=="Spot":
        _settlement(fixed,path,info,state)
    if not synthetic and record:
        with st.popover("Forecast records"):
            st.download_button("Export records",json.dumps(_memory(key),indent=2),"forecast-records.json","application/json",key=state+"_export")
            writer=st.secrets.get("github_forecast_writer_token")
            if writer and repo and st.button("Save and enable daily updates",key=state+"_save"):
                try:
                    with connect(repo,writer) as store:
                        save_records(store,[record]);activate_configuration(store,record)
                    st.success("Configuration saved for daily updates. Issued records remain unchanged.")
                except Exception:
                    st.error("Cloud save failed. Browser record retained.")


@st.cache_data(max_entries=8,show_spinner=False)
def _evolution(stamp,contract,config,target,strict,_fixed):
    return replay_current(_fixed,contract,json.loads(config),1,strict,target)


def _review_chart(fixed,replay,saved,view,key,config,kind,contract,horizon):
    rows=replay.copy()
    if saved is not None:
        if not saved:
            st.info("No actual issued records exist for this selection. No replay is substituted.");return
        ids=sorted(saved,key=lambda i:saved[i]["created_at"],reverse=True)
        chosen=multi("Saved versions",ids,key+"_versions",ids[:1],min(10,len(ids)),format_func=lambda i:saved[i]["created_at"][:16]+" · "+saved[i]["config"].get("model","")+" · "+i[:6])
        batches=[]
        for identity in chosen:
            record=saved[identity];batch=evaluated_record(record,fixed)
            batch["date"]=pd.Timestamp(record["created_at"]).tz_localize(None).normalize()
            batch["forecast"]=batch.scenario
            batch["anchor"]=record["anchor"]
            batch["momentum"]=np.nan;batch["majority"]=np.nan
            batches.append(batch)
        rows=pd.concat(batches,ignore_index=True) if batches else pd.DataFrame()
    elif replay.empty:
        st.info("No eligible forecast review cases.");return
    if rows.empty:
        st.info("No recorded forecasts selected.");return
    if saved is not None:
        scored=rows[rows.horizon.eq(horizon)].dropna(subset=["actual","forecast"]).sort_values("date").drop_duplicates("date",keep="last")
        keep=[];until=pd.Timestamp.min
        for idx,row in scored.iterrows():
            if row.date>=until:keep.append(idx);until=row.target_date
        stats=forecast_score(scored.loc[keep])
        cols=st.columns(4)
        for col,label,value in zip(cols,["Direction Accuracy","Forecast Skill Score","MAE","Samples"],[f"{stats['direction']:.1%}" if np.isfinite(stats['direction']) else "—",f"{stats['skill']:+.1f}%" if np.isfinite(stats['skill']) else "—",f"{stats['mae']:,.0f}" if np.isfinite(stats['mae']) else "—",stats['samples']]):col.metric(label,value)
        st.caption("Scores use only selected issued versions with observed outcomes and non-overlapping target intervals. No current parameters are reapplied.")
    fig=chart("USD/day",height=440)
    if view=="Forecast Evolution":
        low,high=rows.target_date.min().date(),rows.target_date.max().date()
        target=calendar("Target date",key+"_evolution_date",high,low,high)
        selected=rows[rows.target_date.eq(pd.Timestamp(target))].sort_values("date")
        if selected.empty:
            st.info("No forecasts for that target date.");return
        fig.add_scatter(x=selected.date,y=selected.forecast,name="Forecast evolution",mode="lines+markers")
        actual=selected.actual.dropna()
        if len(actual):fig.add_hline(y=float(actual.iloc[-1]),line_dash="dot")
        dates=selected.date
    else:
        dates=rows.target_date
        if view=="Forecast Error":
            fig.add_scatter(x=dates,y=rows.forecast-rows.actual,name="Forecast − actual",mode="lines+markers")
            fig.add_hline(y=0,line_dash="dot")
        else:
            fig.add_scatter(x=dates,y=rows.forecast,name="Forecast",mode="lines+markers")
            fig.add_scatter(x=dates,y=rows.actual,name="Actual",mode="lines+markers")
    _trajectory_axis(fig,pd.DataFrame({"date":dates}))
    display(fig,key+"_review_shared")


def _settlement(fixed,path,info,key):
    months=sorted(map(str,path.target_date.dt.to_period("M").unique()))
    month=pd.Period(choice("Settlement month",months,key+"_settlement_month"),"M")
    expected=sessions(month)
    if expected is None:
        st.caption("Settlement calendar unavailable; no settlement estimate reported.");return
    spot=fixed[fixed.kind.eq("Spot")].drop_duplicates("quote_day",keep="last").set_index("quote_day").price
    forecast=path.set_index("target_date").scenario
    values=pd.Series([spot.get(day,np.nan) if day<=info["asof"] else forecast.get(day,np.nan) for day in expected],index=expected)
    if values.isna().any():
        st.caption("Full monthly coverage is required to estimate settlement; observed Spot is never replaced.");return
    estimate=values.mean()
    bfa=fixed[fixed.kind.eq("BFA")&fixed.tenor.eq("month")&fixed.delivery_start.eq(month.start_time)&fixed.quote_day.eq(info["asof"])]
    price=float(bfa.price.iloc[-1]) if not bfa.empty else np.nan
    st.caption(f"Predicted monthly settlement {estimate:,.0f} · matching BFA {price:,.0f} · gap {estimate-price:+,.0f} USD/day. Reconstructed index mean; no unverified quarter/year aggregation.")
