"""Shared Spot/BFA forecasting and parameter-linked replay. Reuses the journal."""
from hashlib import sha256
import json
import numpy as np
import pandas as pd
import streamlit as st
from .analytics import delivery_label
from .selection import contract_choice, contract_label
from .outlook import DEFAULTS, forecast_path, apply_judgement, prediction_record, replay_current, forecast_score, evaluated_record, future_grid
from .outlook_ui import _memory, _records, _future_chart
from .forecast_store import save_records, activate_configuration
from .preferences import choice, multi, calendar, encode, number
from .settlement import sessions, SUPPORTED
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
        st.info("No compatible USD/day quotes for this benchmark.");return
    latest=current.quote_day.max()
    if kind=="BFA":
        contracts=current.drop_duplicates("identity",keep="last").sort_values("delivery_start")
        ready=contracts[contracts.tenor.eq("month") & contracts.delivery_start.gt(latest)]
        preferred=ready.identity.iloc[0] if not ready.empty else contracts.identity.iloc[-1]
        contract=contract_choice("Contract",contracts,key+"_contract",preferred)
        metadata=contracts.set_index("identity").loc[contract]
        expiry=metadata.delivery_end
        contract_name=contract_label(metadata).split(" · ")[0]
        current=current[current.identity.eq(contract)]
        latest=current.quote_day.max()
    else:
        contract=f"spot:{vessel}:{product}"
        expiry=today+pd.DateOffset(months=3)
        contract_name="Spot"
    state=key+sha256(contract.encode()).hexdigest()[:8]
    selected_model=choice("Model",["Difference","Ratio","Exploratory"],state+"_method",control="segmented_control")
    base_key=sha256((state+"_basic_model").encode()).hexdigest()[:12]
    memory=st.session_state.setdefault('_ffa_preferences',{})
    if selected_model!="Exploratory":memory[base_key]=selected_model
    model=memory.get(base_key,"Difference")
    exploratory=selected_model=="Exploratory"
    window=choice("Reference Window",[5,10,20,30,60,"Custom"],state+"_window",20)
    config={**DEFAULTS,"model":model,"target_kind":kind,"weighting":"Equal weighted",
            "year_mode":"Rolling years","year_anchor":latest.year,"pooled":kind=="BFA","forecast_end":str(expiry.date()),"range_mode":"Auto"}
    if window=="Custom":
        a=calendar("Reference from",state+"_from",max(current.quote_day.min().date(),(latest-pd.Timedelta(days=28)).date()),current.quote_day.min().date(),latest.date())
        b=calendar("Reference to",state+"_to",latest.date(),a,latest.date())
        config.update(custom=[str(a),str(b)],window=max(1,int(current.quote_day.between(pd.Timestamp(a),pd.Timestamp(b)).sum())))
    else:config["window"]=window
    horizon_choice=choice("Prediction Horizon",[1,5,10,20,"Custom"],state+"_prediction_horizon",5,control="segmented_control",format_func=lambda n:f"{n}D" if isinstance(n,int) else n)
    view=choice("Chart view",["Future Forecast","Forecast vs Actual","Forecast Error","Forecast Evolution"],state+"_view",control="segmented_control")
    mode=choice("Review data",["Historical Replay","Saved Forecasts"],state+"_review_mode",control="segmented_control") if view!="Future Forecast" else "Historical Replay"
    saved_mode=mode=="Saved Forecasts"
    stamp=(stamp,len(fixed),kind)
    path,info=_projection(stamp,contract,json.dumps(config,sort_keys=True),fixed)
    if info.get('candidate_years'):
        years=multi("Reference Years",info["candidate_years"],state+"_years",info.get("reference_years",info['candidate_years'][:5]),len(info["candidate_years"]))
        if not years:
            st.info("Select at least one historical reference year.");return
        config["years"]=list(years)
        if path.empty or list(years)!=info['reference_years']:
            path,info=_projection(stamp,contract,json.dumps(config,sort_keys=True),fixed)
    if path.empty and view=='Future Forecast':
        st.info(info.get("reason","No historical reference coverage."))
        st.caption(f"{vessel} {product} · {kind} coverage {current.quote_day.min():%d %b %Y}–{latest:%d %b %Y}. Benchmarks are not spliced together.")
        return
    # Dates are based on the relevant publication calendar, not compressed
    # non-missing forecasts. Missing forecast values never shorten the horizon.
    dates=pd.DatetimeIndex(path.target_date) if not path.empty else future_grid(latest,expiry,kind)[0]
    horizon=int(horizon_choice) if horizon_choice!="Custom" else 5
    target=None
    if horizon_choice=="Custom" and len(dates):
        chosen=calendar("Prediction date",state+"_prediction_date",dates[min(4,len(dates)-1)].date(),dates[0].date(),dates[-1].date())
        position=dates.searchsorted(pd.Timestamp(chosen))
        if position<len(dates):
            horizon=int(position)+1
            if dates[position].normalize()!=pd.Timestamp(chosen):st.caption(f"Next indicative quotation date: {dates[position]:%d %b %Y}.")
    elif horizon_choice=="Custom":
        st.info("Custom needs a remaining quotation-date range. Choose a preset horizon to review an expired contract.");return
    if horizon<=len(dates):target=dates[horizon-1]
    basic_json=json.dumps(config,sort_keys=True)
    if exploratory and not path.empty:
        st.caption(f"Scenario on the {model} base. Initial adjustment = direction × strength × maximum; it fades until the end date. This is a judgement, not a probability.")
        config["judgement"]=choice("Market judgement",["Neutral","Bearish","Bullish"],state+"_judgement",control="segmented_control")
        config["strength"]=number("Judgement strength",state+"_strength",.3,0.,1.,.1)
        with st.popover("Scenario settings"):
            config["maximum_pct"]=number("Maximum adjustment (%)",state+"_max",10.,0.,100.,1.)
            config["reason"]=st.text_input("Reason",key=state+"_reason")
            end=path.target_date.max().date()
            default=min(end,max(today.date(),(latest+pd.offsets.BDay(20)).date()))
            start=min(today.date(),end)
            deadline=calendar("Judgement ends",state+"_deadline",default,start,end)
            config.update(judgement_from=str(start),judgement_until=str(deadline),fade=True)
    if not path.empty:path=path.assign(scenario=apply_judgement(path,config))
    replay=pd.DataFrame()
    score=None
    # Only complete availability metadata enables timestamp-gated replay. The
    # archived default remains explicitly exploratory; no selector implies proof.
    strict=bool(fixed.available_at.notna().all())
    if not saved_mode and view!="Forecast Evolution" and (target is not None or view!='Future Forecast'):
        with st.spinner("Updating forecast and historical results…"):
            replay=_replay(stamp,contract,basic_json,horizon,strict,fixed)
        if not replay.empty:
            calendar_days=sorted(fixed.loc[fixed.kind.eq(kind),"quote_day"].unique())[-1500:]
            cutoff=pd.Timestamp(calendar_days[int(len(calendar_days)*.7)])
            replay=replay[replay.date.ge(cutoff)]
        score=forecast_score(replay)
    if not path.empty:
        _conclusion(path,info,target,horizon,vessel,contract_name,exploratory,score)
        formula="C(t) + A − B" if model=="Difference" else "C(t) × A / B"
        st.caption(f"{formula} · A {info['level']:,.0f} · B {info['historical_reference']:,.0f} · Reference years {', '.join(map(str,info['reference_years']))}. Hover shows actual contributors.")
        if len(info["candidate_years"])<5:
            st.caption(f"{len(info['candidate_years'])} matching historical year(s). {kind} data: {current.quote_day.min():%d %b %Y}–{latest:%d %b %Y}; other benchmarks are kept separate.")
        if (today-latest).days>7:st.warning(f"Latest quote is {latest:%d %b %Y}. Inputs are stale; the selected horizon starts from that quote, not today.")
        record=prediction_record(path,info,contract,config,info["source_revision"])
        if record and not synthetic:record=_memory(key).setdefault(record["id"],encode(record))
    else:record=None
    if score is not None:
        _scores(score)
        if score['samples']<10:st.caption("Fewer than 10 non-overlapping outcomes: historical scores are unavailable.")
        elif np.isfinite(score['majority_accuracy']):
            st.caption(f"Same-horizon historical direction baseline: {score['majority_accuracy']:.0%}. Direction accuracy is not the probability of this forecast rising.")
        st.caption(("Timestamp-gated replay" if strict else "Date replay · publication times/revisions unverified")+" · chronological holdout, same historical year lags at each origin. Repeated tuning is exploratory.")
    if view=="Future Forecast":
        future=path[path.target_date.gt(today)]
        if future.empty or not future.forecast.notna().any():
            st.info("No valid future points remain within the contract and available historical coverage.")
        else:
            _future_chart(current,future,info,"Price path","Exploratory scenario" if exploratory else "Basic",False,[],state)
            st.caption("Dates are indicative quotation dates; future calendars and contract trading availability may change.")
    else:
        saved=_records(key,contract,repo,credential_id,token) if saved_mode else None
        evolution_target=None
        if view=="Forecast Evolution":
            low=current.quote_day.min().date();high=info.get('expiry',expiry).date()
            if saved:
                target_days=[pd.Timestamp(p["target_date"]).date() for record in saved.values() for p in record["points"]]
                if target_days:low,high=min(target_days),max(target_days)
            default=max(low,min(high,target.date() if target is not None else high))
            evolution_target=calendar("Evolution Target Date",state+"_target_day",default,low,high)
            if not saved_mode:
                with st.spinner("Updating predictions for this target…"):
                    replay=_evolution(stamp,contract,basic_json,str(evolution_target),strict,fixed)
        if exploratory and not saved_mode:st.caption("Replay evaluates the base model. Subjective changes have no historical observations; review saved scenarios prospectively.")
        _review_chart(fixed,replay,saved,view,state,config,kind,contract,horizon,evolution_target)
    if kind=="Spot" and not path.empty:_settlement(fixed,path,info,state,target)
    if not synthetic and record:
        with st.popover("Forecast records"):
            st.download_button("Export records",json.dumps(_memory(key),indent=2),"forecast-records.json","application/json",key=state+"_export")
            writer=st.secrets.get("github_forecast_writer_token")
            if writer and repo and st.button("Save and enable daily updates",key=state+"_save"):
                try:
                    with connect(repo,writer) as store:
                        save_records(store,[record]);activate_configuration(store,record)
                    st.success("Saved for daily updates. Issued records remain unchanged.")
                except Exception:st.error("Cloud save failed. Browser record retained.")


def _scores(score):
    cols=st.columns(4)
    for col,label,value in zip(cols,["Direction Accuracy","Forecast Skill Score","MAE","Samples"],
        [f"{score['direction']:.1%}" if np.isfinite(score['direction']) else "—",
         f"{score['skill']:+.1f}%" if np.isfinite(score['skill']) else "—",
         f"{score['mae']:,.0f}" if np.isfinite(score['mae']) else "—",score['samples']]):col.metric(label,value)


def _conclusion(path,info,target,horizon,vessel,name,exploratory,score):
    st.markdown(f"**{vessel} {name} — {horizon} Trading Days Ahead**")
    if target is None:
        st.info("Selected horizon exceeds the remaining contract or forecast range.");return
    point=path[path.target_date.eq(target)]
    if point.empty or not np.isfinite(point.scenario.iloc[0]):
        st.info(f"No valid historical counterpart at {target:%d %b %Y}. The target date is not replaced by a later valid forecast.");return
    current=float(info['latest']);predicted=float(point.scenario.iloc[0]);change=predicted-current
    outlook="Bullish" if change>1e-8 else "Bearish" if change< -1e-8 else "Flat"
    percent=f" ({change/abs(current):+.1%})" if abs(current)>1e-8 else ""
    st.markdown(f"Current Price **${current:,.0f}** → Forecast Price **${predicted:,.0f}** · Expected Change **${change:+,.0f}{percent}** · Outlook **{outlook}**")
    st.caption(f"Target {target:%d %b %Y} · current quote {info['asof']:%d %b %Y}. Direction compares the target price with the latest real quote, not the forecast slope.")
    if exploratory:st.caption("This includes a subjective scenario. Historical scores below belong to the unchanged base model; the adjustment has no proven historical accuracy.")
    elif score is None or score['samples']<10 or not np.isfinite(score['skill']) or score['skill']<=0:
        st.caption("This is the model's current price view; historical evidence does not establish a reliable predictive advantage.")
    else:st.caption("The model reduced historical price error versus unchanged price in this replay; that does not establish a reliable trading signal.")


@st.cache_data(max_entries=8,show_spinner=False)
def _evolution(stamp,contract,config,target,strict,_fixed):
    return replay_current(_fixed,contract,json.loads(config),1,strict,target)


def _review_chart(fixed,replay,saved,view,key,config,kind,contract,horizon,target=None):
    rows=replay.copy()
    if saved is not None:
        if not saved:
            st.info("No actual issued records exist for this selection. No replay is substituted.");return
        ids=sorted(saved,key=lambda i:saved[i]["created_at"],reverse=True)
        chosen=multi("Saved versions",ids,key+"_versions",ids[:10] if view=="Forecast Evolution" else ids[:1],min(10,len(ids)),format_func=lambda i:saved[i]["created_at"][:16]+" · "+saved[i]["config"].get("model","")+" · "+i[:6])
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
        st.info("No eligible replay cases: check reference-year policy, target date within contract lifetime, matured outcomes, and publication timestamps in strict mode. Fixed-year selections can be unavailable at early origins.");return
    if rows.empty:
        st.info("No recorded forecasts selected.");return
    if saved is not None and view!="Forecast Evolution":
        scored=rows[rows.horizon.eq(horizon)].dropna(subset=["actual","forecast"]).sort_values("date").drop_duplicates("date",keep="last")
        keep=[];until=pd.Timestamp.min
        for idx,row in scored.iterrows():
            if row.date>=until:keep.append(idx);until=row.target_date
        stats=forecast_score(scored.loc[keep])
        _scores(stats)
        st.caption("Scores use only selected issued versions with observed outcomes and non-overlapping target intervals. No current parameters are reapplied.")
    fig=chart("USD/day",height=440)
    if view=="Forecast Evolution":
        selected=rows[rows.target_date.eq(pd.Timestamp(target))].sort_values("date")
        if selected.empty:
            st.info("No eligible predictions for this target date. Check that it follows the prediction origins, is within the fixed contract lifetime, and has historical seasonal coverage; strict mode additionally requires publication timestamps.");return
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


def _settlement(fixed,path,info,key,target=None):
    if info['product'] not in SUPPORTED:
        st.caption("Monthly settlement rules are not verified for this benchmark; no settlement estimate is reported.");return
    months=sorted(map(str,path.target_date.dt.to_period("M").unique()))
    month=pd.Period(target,"M") if target is not None else pd.Period(months[0],"M")
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
    st.caption(f"{month} · Predicted monthly settlement {estimate:,.0f} · matching BFA {price:,.0f} · gap {estimate-price:+,.0f} USD/day. Reconstructed index mean; no unverified quarter/year aggregation.")
