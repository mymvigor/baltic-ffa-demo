"""Browser-persistent batch grid. Only an explicitly saved batch reaches Python."""
from streamlit.components.v2 import component

CSS='''
.wrap{font:14px Arial,sans-serif;color:#243b52}.scroll{overflow:auto;border:1px solid #dce5ed;border-radius:8px}
table{border-collapse:collapse;width:100%;min-width:880px}th{background:#edf3f6;text-align:left;padding:9px 7px;font-size:12px;white-space:nowrap}
td{padding:2px;border-top:1px solid #e7edf3}input,select{box-sizing:border-box;width:100%;min-width:70px;border:1px solid transparent;border-radius:3px;background:transparent;color:#243b52;font:inherit;padding:7px 5px}
input:focus,select:focus{border-color:#18836f;outline:none;background:white}input[type=date]{min-width:126px}input.contract{min-width:245px}
.bad{border-color:#bd4656!important;background:#fff5f5}button{border:0;color:#64798b;background:none;cursor:pointer;padding:6px}button:hover{color:#bd4656}
.status{font-size:13px;color:#52677b;margin:0 0 8px}.error{color:#bd4656;font-size:12px;margin:6px 0}.deleted{opacity:.5;text-decoration:line-through}.hint{font-size:12px;color:#718292;margin-top:6px}
.locked{opacity:.7;pointer-events:none}input:disabled,select:disabled{opacity:.8}
.save{background:#18836f;color:white;padding:8px 15px;border-radius:6px;float:right}.save:disabled{background:#b4c2c9;cursor:default}.save:hover{color:white}
'''

JS=r'''
export default function({data,parentElement,setStateValue}) {
  const host=parentElement.querySelector('.wrap');
  const names=['Trade Date','Contract','Side','Price','Quantity','Fees','Notes'];
  const labels=data.catalog, known=new Map(labels.map(c=>[c.search,c]));
  const records=new Map(data.trades.map(t=>[t.id,t]));
  const storeKey='ffa-position-draft-v2:'+data.account;
  let tab;
  try { tab=sessionStorage.getItem('ffa-position-tab');if(!tab){tab=crypto.randomUUID();sessionStorage.setItem('ffa-position-tab',tab);} }
  catch {tab=crypto.randomUUID();}
  let local=null, storageFailed=false, locked=false, sentVersion=null, nextRetry=0,attempt=0;
  const fresh=()=>({version:crypto.randomUUID(),edits:{},deletes:{},updated:Date.now(),lease:{owner:tab,until:0}});
  const read=()=>{try{const v=JSON.parse(localStorage.getItem(storeKey));return v&&v.edits&&v.deletes?v:(local||fresh());}catch{return local||fresh();}};
  let draft=read();
  const persist=()=>{local=draft;try{localStorage.setItem(storeKey,JSON.stringify(draft));}catch{storageFailed=true;}};
  const owns=()=>!draft.lease||draft.lease.owner===tab||draft.lease.until<Date.now();
  const isLocked=()=>locked||(draft.savingUntil||0)>Date.now();
  const pending=()=>Object.keys(draft.edits).length+Object.keys(draft.deletes).length+(draft.settings?1:0);
  const raw=t=>({'Trade Date':t.date,'Contract':labels.find(c=>c.contract===t.contract)?.search||t.label,'Side':t.side,'Price':t.price,'Quantity':t.days,'Fees':t.fees||0,'Notes':t.notes||''});
  const rememberedQuantity=()=>{try{const n=Number(localStorage.getItem(storeKey+':quantity'));if(Number.isFinite(n)&&n>0)return n;}catch{}return data.last_quantity||5;};
  const blank=()=>({'Trade Date':data.today,'Contract':'','Side':'Buy','Price':'','Quantity':rememberedQuantity(),'Fees':'','Notes':''});
  const normalized=x=>String(x).toUpperCase().replace(/[^A-Z0-9]/g,'');
  const contract=x=>known.get(x)||(()=>{const a=labels.filter(c=>normalized(x)===normalized(c.vessel+' '+c.label.split(' · ')[0]));return a.length===1?a[0]:null;})();
  const numeric=x=>Number(String(x).replace(/[$,]/g,''));
  const issues=()=>{const out=[];for(const [id,e] of Object.entries(draft.edits)){
    const v=e.fields;const c=contract(v.Contract);let error='';
    if(!/^\d{4}-\d{2}-\d{2}$/.test(v['Trade Date'])||v['Trade Date']>data.today)error='A valid, non-future Trade Date is required.';
    else if(!c)error='Select a unique fixed contract.';
    else if(!c.quantity_unit||c.multiplier==null)error='Quantity unit or multiplier is unverified.';
    else if(!['Buy','Sell'].includes(v.Side))error='Select Buy or Sell.';
    else if(v.Price===''||!Number.isFinite(numeric(v.Price))||numeric(v.Price)<0)error='Price is required.';
    else if(v.Quantity===''||!Number.isFinite(numeric(v.Quantity))||numeric(v.Quantity)<=0)error='Quantity must be positive.';
    else if(v.Fees!==''&&(!Number.isFinite(numeric(v.Fees))||numeric(v.Fees)<0))error='Fees must be non-negative.';
    if(error)out.push([id,error]);
  }return out;};
  const payload=()=>({edits:draft.edits,deletes:draft.deletes,...(draft.settings?{settings:draft.settings}:{})});
  if(data.ack&&data.ack.version===draft.version){draft=fresh();persist();}
  if(data.settings_change&&data.settings_change.value!==data.settings_change.base){
    const desired=data.settings_change;
    if(!draft.settings||draft.settings.value!==desired.value){draft.settings=desired;draft.version=crypto.randomUUID();draft.updated=Date.now();persist();}
  }
  host.replaceChildren();
  const status=document.createElement('div');status.className='status';
  const error=document.createElement('div');error.className='error';
  const saveButton=document.createElement('button');saveButton.className='save';saveButton.textContent='Save changes';
  if(data.mode==='entry'){host.append(saveButton);host.append(status);}
  const scroll=document.createElement('div');scroll.className='scroll';host.append(scroll);
  const table=document.createElement('table');scroll.append(table);
  const head=document.createElement('thead');table.append(head);
  const hr=document.createElement('tr');head.append(hr);
  for(const n of [...names,'']){const th=document.createElement('th');th.textContent=n;hr.append(th);}
  const body=document.createElement('tbody');table.append(body);
  const list=document.createElement('datalist');list.id='contracts-'+crypto.randomUUID();host.append(list);
  for(const c of labels){const option=document.createElement('option');option.value=c.search;list.append(option);}
  host.append(error);
  if(data.mode==='entry'){
    const hint=document.createElement('div');hint.className='hint';hint.textContent='Paste rows from Excel, then Save changes to submit the batch. Quantity is in days for USD/day TC contracts.';host.append(hint);
  }
  const rowNodes=new Map();let entryRows=[];
  const notify=()=>window.dispatchEvent(new CustomEvent('ffa-position-draft',{detail:storeKey}));
  const change=()=>{draft.version=crypto.randomUUID();draft.updated=Date.now();draft.authorizedVersion=null;draft.attempt=0;draft.retryAt=0;draft.savingUntil=0;sentVersion=null;nextRetry=0;attempt=0;persist();notify();refreshStatus();};
  const rowFields=tr=>Object.fromEntries(names.map(n=>[n,tr.querySelector('[data-field="'+n+'"]')?.value??'']));
  const saveRow=tr=>{
    draft=read();if(!owns()||isLocked())return;
    const id=tr.dataset.id;const original=records.get(id)||null;const old=draft.edits[id];const values=rowFields(tr);
    const quantity=numeric(values.Quantity);
    if(Number.isFinite(quantity)&&quantity>0){
      try{localStorage.setItem(storeKey+':quantity',String(quantity));}catch{}
      for(const other of entryRows){if(other!==id&&!draft.edits[other]){const input=rowNodes.get(other)?.querySelector('[data-field="Quantity"]');if(input)input.value=String(quantity);}}
    }
    if(original&&names.every(n=>String(raw(original)[n]??'')===String(values[n]??''))){delete draft.edits[id];}
    else draft.edits[id]={base:old?old.base:original,fields:values,order:old?.order||Date.now()*1000+entryRows.indexOf(id)};
    change();
    if(data.mode==='entry'&&entryRows.indexOf(id)===entryRows.length-1&&(values.Contract||values.Price))addEntry();
  };
  function renderRow(id,values,history=false){
    const tr=document.createElement('tr');tr.dataset.id=id;body.append(tr);rowNodes.set(id,tr);
    for(const name of names){
      const td=document.createElement('td');tr.append(td);
      const input=document.createElement(name==='Side'?'select':'input');input.dataset.field=name;
      if(name==='Side'){for(const s of ['Buy','Sell']){const o=document.createElement('option');o.value=o.textContent=s;input.append(o);}}
      else if(name==='Trade Date'){input.type='date';input.max=data.today;}
      else if(name==='Contract'){input.setAttribute('list',list.id);input.className='contract';input.placeholder='Search PMX DEC26…';}
      else if(['Price','Quantity','Fees'].includes(name)){input.inputMode='decimal';input.style.minWidth='80px';}
      else{input.placeholder='Optional';}
      input.value=values[name]??'';
      input.addEventListener('input',()=>saveRow(tr));input.addEventListener('change',()=>saveRow(tr));
      input.addEventListener('keydown',e=>{draft=read();if(!owns()||isLocked()){e.preventDefault();return;}draft.updated=Date.now();persist();
        if(e.key==='Enter'){e.preventDefault();const rows=[...body.rows],i=rows.indexOf(tr),j=names.indexOf(name);if(i===rows.length-1&&!history)addEntry();body.rows[i+1]?.cells[j]?.querySelector('input,select')?.focus();}
      });
      input.addEventListener('paste',e=>{
        const text=e.clipboardData?.getData('text/plain')||'';if(!/[\t\n]/.test(text))return;e.preventDefault();
        const cells=text.trimEnd().split(/\r?\n/).map(l=>l.split('\t'));let start=names.indexOf(name),r=[...body.rows].indexOf(tr);
        if(cells[0].some(c=>c==='Trade Date')&&cells[0].includes('Contract')){cells.shift();start=0;}
        for(const row of cells){if(r>=body.rows.length){if(history)break;addEntry();}const target=body.rows[r++];
          for(let c=0;c<row.length&&start+c<names.length;c++){
            const n=names[start+c],field=target.querySelector('[data-field="'+n+'"]');let v=row[c];
            if(n==='Side')v=v.toLowerCase()==='sell'?'Sell':v.toLowerCase()==='buy'?'Buy':v;
            if(n==='Trade Date'){const m=v.match(/^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$/);if(m)v=m[1]+'-'+m[2].padStart(2,'0')+'-'+m[3].padStart(2,'0');}
            field.value=v;
          }saveRow(target);
        }
      });
      td.append(input);
    }
    const td=document.createElement('td');tr.append(td);const undo=document.createElement('button');undo.textContent=history?'Delete':'×';undo.title=history?'Delete transaction':'Remove pending row';td.append(undo);
    undo.addEventListener('click',()=>{draft=read();if(!owns()||isLocked())return;
      if(history){if(!window.confirm('Delete this recorded transaction? P&L will be recalculated.'))return;draft.deletes[id]=draft.edits[id]?.base||records.get(id);delete draft.edits[id];tr.classList.add('deleted');}
      else{delete draft.edits[id];entryRows=entryRows.filter(k=>k!==id);rowNodes.delete(id);tr.remove();if(!entryRows.length)addEntry();}
      change();
    });
    if(history){const cancel=document.createElement('button');cancel.textContent='Undo';cancel.title='Discard this row’s unsynced edit or deletion';td.append(cancel);
      cancel.addEventListener('click',()=>{draft=read();if(!owns()||isLocked())return;delete draft.edits[id];delete draft.deletes[id];change();renderAll();});}
    if(draft.deletes[id])tr.classList.add('deleted');
  }
  function addEntry(id=null,values=null){const identity=id||crypto.randomUUID();entryRows.push(identity);renderRow(identity,values||blank());}
  function renderAll(){body.replaceChildren();rowNodes.clear();entryRows=[];
    if(data.mode==='entry'){
      for(const [id,e] of Object.entries(draft.edits)){if(!e.base)addEntry(id,e.fields);}
      addEntry();if(entryRows.length<2)addEntry();
    }else{
      const ids=new Set([...data.visible,...Object.entries(draft.edits).filter(([id,e])=>e.base).map(([id])=>id),...Object.keys(draft.deletes)]);
      for(const id of ids){const t=records.get(id);if(t)renderRow(id,draft.edits[id]?.fields||raw(t),true);}
    }
    refreshStatus();
  }
  function refreshStatus(){
    const errors=issues();const blocked=new Set(errors.map(e=>e[0]));
    for(const [id,tr] of rowNodes){tr.querySelectorAll('input,select').forEach(input=>{input.classList.toggle('bad',blocked.has(id));input.disabled=isLocked()||!owns()||!!draft.deletes[id];});}
    if(data.mode!=='entry')return;
    saveButton.disabled=!pending()||!data.writable||errors.length>0||isLocked()||!owns();
    scroll.classList.toggle('locked',isLocked()||!owns());
    if(isLocked())status.textContent='Saving...';
    else if(!owns())status.textContent='Pending · This browser draft is open in another tab';
    else if(pending())status.textContent=data.failure&&data.failure.version===draft.version?'Save failed':'Pending';
    else status.textContent=data.last_sync?'Saved · '+data.last_sync:'Pending';
    error.textContent=storageFailed?'Browser storage is unavailable. Keep this tab open.':errors.length?errors[0][1]+' Batch not saved.':
      data.failure&&data.failure.version===draft.version?data.failure.message:pending()&&!data.writable?'Private write permission is not configured. The pending batch remains in this browser.':'';
  }
  renderAll();
  function submit(){
    draft=read();if(!owns()||isLocked()||!pending()||!data.writable||issues().length)return;
    draft.authorizedVersion=draft.version;
    locked=true;draft.savingUntil=Date.now()+180000;draft.submittedSession=data.session;draft.attempt=(draft.attempt||0)+1;persist();sentVersion=draft.version;attempt=draft.attempt;
    notify();refreshStatus();
    setStateValue('batch',{version:draft.version,payload:payload(),attempt,submitted_at:Date.now()});
  }
  saveButton.addEventListener('click',submit);
  const external=e=>{if(e.detail&&e.detail!==storeKey)return;draft=read();if(data.mode==='history')refreshStatus();else refreshStatus();};
  const storage=e=>{if(e.key===storeKey){draft=read();renderAll();}};
  window.addEventListener('ffa-position-draft',external);window.addEventListener('storage',storage);
  let leaseTime=0;
  const timer=setInterval(()=>{
    draft=read();
    if(data.mode!=='entry'){refreshStatus();return;}
    if(!owns()){refreshStatus();return;}
    if(draft.authorizedVersion===draft.version&&draft.attempt>0&&draft.submittedSession!==data.session){
      locked=false;draft.savingUntil=0;draft.submittedSession=data.session;draft.retryAt=Date.now()+1000;persist();
    }
    if(Date.now()-leaseTime>5000){draft.lease={owner:tab,until:Date.now()+15000};persist();leaseTime=Date.now();}
    if(data.failure&&data.failure.version===draft.version&&data.failure.attempt===draft.attempt&&draft.failedAttempt!==draft.attempt){
      locked=false;draft.savingUntil=0;draft.failedAttempt=draft.attempt;draft.retryAt=Date.now()+Math.min(120000,5000*2**Math.min(draft.attempt||0,4));persist();
    }
    // Retry only the unchanged batch already authorized by a Save click.
    if(draft.authorizedVersion!==draft.version||!draft.retryAt||!pending()||!data.writable||issues().length||isLocked()||Date.now()<(draft.retryAt||0)){refreshStatus();return;}
    if(sentVersion===draft.version&&!data.failure)return;
    submit();
  },500);
  return ()=>{clearInterval(timer);window.removeEventListener('ffa-position-draft',external);window.removeEventListener('storage',storage);};
}
'''

grid=component('ffa_position_batch_grid',html='<div class="wrap"></div>',css=CSS,js=JS)
