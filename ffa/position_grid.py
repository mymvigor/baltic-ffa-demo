"""Browser-persistent batch grid. Only an explicitly saved batch reaches Python."""
from streamlit.components.v2 import component

CSS='''
.wrap{font:13px Arial,sans-serif;color:#243b52;width:100%;min-width:0;container-type:inline-size}.scroll{width:100%;box-sizing:border-box;border:1px solid #dce5ed;border-radius:8px}
table{border-collapse:collapse;width:100%;table-layout:fixed}th{background:#edf3f6;text-align:left;padding:9px 5px;font-size:12px;overflow-wrap:anywhere}
th,td{box-sizing:border-box}td{padding:2px;border-top:1px solid #e7edf3;min-width:0}input,select{box-sizing:border-box;width:100%;max-width:100%;min-width:0;border:1px solid transparent;border-radius:3px;background:transparent;color:#243b52;font:inherit;padding:7px 3px}
input:focus,select:focus{border-color:#18836f;outline:none;background:white}input[type=date]{min-width:0;font-size:12px}input.contract{min-width:0}
.bad{border-color:#bd4656!important;background:#fff5f5}button{border:0;color:#64798b;background:none;cursor:pointer;padding:6px}button:hover{color:#bd4656}
.status{font-size:13px;color:#52677b;margin:0 0 8px}.error{color:#bd4656;font-size:12px;margin:6px 0}.deleted{opacity:.5;text-decoration:line-through}.hint{font-size:12px;color:#718292;margin-top:6px}
.locked{opacity:.7;pointer-events:none}input:disabled,select:disabled{opacity:.8}
.toolbar{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px}.toolbar .status{margin:0}.actions{display:flex;gap:8px}.add{border:1px solid #c4d3dc;border-radius:6px;padding:7px 13px;color:#243b52}
.save{background:#18836f;color:white;padding:8px 15px;border-radius:6px}.save:disabled,.add:disabled{background:#d6e0e6;color:#718292;cursor:default}.save:hover{color:white}
.row-actions button{font-size:11px;padding:5px 2px;max-width:100%}
@container(max-width:820px){thead{display:none}table,tbody{display:block;width:100%}tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));border-bottom:1px solid #dce5ed}td{display:block;border:0}td:before{content:attr(data-label);display:block;font-size:11px;color:#64798b;padding:4px 3px 0}.row-actions{grid-column:1/-1;text-align:right}.row-actions:before{display:none}}
'''

JS=r'''
export default function({data,parentElement,setStateValue}) {
  const host=parentElement.querySelector('.wrap');
  const names=['Trade Date','Contract','Side','Price','Quantity','Fees','Notes'];
  const labels=data.catalog, known=new Map(labels.map(c=>[c.search,c]));
  const records=new Map(data.trades.map(t=>[t.id,t]));
  const legacyKey='ffa-position-draft-v2:'+data.account;
  const storeKey='ffa-position-draft-v3:'+data.account+':'+data.mode;
  const quantityKey=legacyKey+':quantity';
  let tab;
  try { tab=sessionStorage.getItem('ffa-position-tab');if(!tab){tab=crypto.randomUUID();sessionStorage.setItem('ffa-position-tab',tab);} }
  catch {tab=crypto.randomUUID();}
  let local=null, storageFailed=false, locked=false,attempt=0;
  const fresh=()=>({version:crypto.randomUUID(),edits:{},deletes:{},blankRows:[],updated:Date.now(),lease:{owner:tab,until:0}});
  // Split legacy shared drafts once, preserving IDs and unfinished fields.
  // Previously authorized background retries are deliberately not migrated.
  try {
    const old=JSON.parse(localStorage.getItem(legacyKey));
    if(old&&!old.migrated_to_v3){
      const parts={};
      for(const mode of ['entry','history']){
        const part=fresh();part.edits=Object.fromEntries(Object.entries(old.edits||{}).filter(([id,e])=>mode==='entry'?!e.base:!!e.base));
        if(mode==='entry'){part.blankRows=old.blankRows||[];if(old.settings)part.settings=old.settings;}
        else part.deletes=old.deletes||{};
        parts[mode]=part;
      }
      local=parts[data.mode];
      for(const mode of ['entry','history']){
        const target='ffa-position-draft-v3:'+data.account+':'+mode;
        if(!localStorage.getItem(target))localStorage.setItem(target,JSON.stringify(parts[mode]));
      }
      localStorage.setItem(legacyKey,JSON.stringify({...old,migrated_to_v3:true}));
    }
  }catch{storageFailed=true;}
  const read=()=>{try{const v=JSON.parse(localStorage.getItem(storeKey));return v&&v.edits&&v.deletes?v:(local||fresh());}catch{return local||fresh();}};
  let draft=read();
  const persist=()=>{local=draft;try{localStorage.setItem(storeKey,JSON.stringify(draft));}catch{storageFailed=true;}};
  const owns=()=>!draft.lease||draft.lease.owner===tab||draft.lease.until<Date.now();
  const isLocked=()=>locked||(draft.savingUntil||0)>Date.now();
  const pending=()=>Object.keys(draft.edits).length+Object.keys(draft.deletes).length+(draft.settings?1:0);
  const raw=t=>({'Trade Date':t.date,'Contract':labels.find(c=>c.contract===t.contract)?.search||t.label,'Side':t.side,'Price':t.price,'Quantity':t.days,'Fees':t.fees||0,'Notes':t.notes||''});
  const rememberedQuantity=()=>{try{const n=Number(localStorage.getItem(quantityKey));if(Number.isFinite(n)&&n>0)return n;}catch{}return data.last_quantity||5;};
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
  if(data.mode==='entry'&&data.settings_change&&data.settings_change.value!==data.settings_change.base){
    const desired=data.settings_change;
    if(!draft.settings||draft.settings.value!==desired.value){draft.settings=desired;draft.version=crypto.randomUUID();draft.updated=Date.now();persist();}
  }
  host.replaceChildren();
  const status=document.createElement('div');status.className='status';
  const error=document.createElement('div');error.className='error';
  const saveButton=document.createElement('button');saveButton.className='save';saveButton.textContent=data.mode==='entry'?'Save Trades':'Save Changes';
  const addButton=document.createElement('button');addButton.className='add';addButton.textContent='Add row';
  const toolbar=document.createElement('div');toolbar.className='toolbar';toolbar.append(status);const actions=document.createElement('div');actions.className='actions';
  if(data.mode==='entry')actions.append(addButton);actions.append(saveButton);toolbar.append(actions);
  if(data.mode==='entry')host.append(toolbar);
  const scroll=document.createElement('div');scroll.className='scroll';host.append(scroll);
  const table=document.createElement('table');scroll.append(table);
  const head=document.createElement('thead');table.append(head);
  const hr=document.createElement('tr');head.append(hr);
  const widths=[14,26,8,12,10,8,16,6];
  for(const [i,n] of [...names,''].entries()){const th=document.createElement('th');th.textContent=n;th.style.width=widths[i]+'%';hr.append(th);}
  const body=document.createElement('tbody');table.append(body);
  const list=document.createElement('datalist');list.id='contracts-'+crypto.randomUUID();host.append(list);
  for(const c of labels){const option=document.createElement('option');option.value=c.search;list.append(option);}
  host.append(error);
  if(data.mode==='history')host.append(toolbar);
  if(data.mode==='entry'){
    const hint=document.createElement('div');hint.className='hint';hint.textContent='Paste rows from Excel, then Save Trades to submit this batch. Quantity is in days for USD/day TC contracts.';host.append(hint);
  }
  const rowNodes=new Map();let entryRows=[];
  const notify=()=>window.dispatchEvent(new CustomEvent('ffa-position-draft',{detail:storeKey}));
  const change=()=>{draft.version=crypto.randomUUID();draft.updated=Date.now();draft.attempt=0;draft.savingUntil=0;attempt=0;persist();notify();refreshStatus();};
  const rowFields=tr=>Object.fromEntries(names.map(n=>[n,tr.querySelector('[data-field="'+n+'"]')?.value??'']));
  const saveRow=tr=>{
    draft=read();if(!owns()||isLocked())return;
    const id=tr.dataset.id;const original=records.get(id)||null;const old=draft.edits[id];const values=rowFields(tr);
    const quantity=numeric(values.Quantity);
    if(Number.isFinite(quantity)&&quantity>0){
      try{localStorage.setItem(quantityKey,String(quantity));}catch{}
      for(const other of entryRows){if(other!==id&&!draft.edits[other]){const input=rowNodes.get(other)?.querySelector('[data-field="Quantity"]');if(input)input.value=String(quantity);}}
    }
    if(original&&names.every(n=>String(raw(original)[n]??'')===String(values[n]??''))){delete draft.edits[id];}
    else draft.edits[id]={base:old?old.base:original,fields:values,order:old?.order||Date.now()*1000+entryRows.indexOf(id)};
    draft.blankRows=(draft.blankRows||[]).filter(k=>k!==id);
    change();
  };
  function renderRow(id,values,history=false){
    const tr=document.createElement('tr');tr.dataset.id=id;body.append(tr);rowNodes.set(id,tr);
    for(const name of names){
      const td=document.createElement('td');td.dataset.label=name;tr.append(td);
      const input=document.createElement(name==='Side'?'select':'input');input.dataset.field=name;
      if(name==='Side'){for(const s of ['Buy','Sell']){const o=document.createElement('option');o.value=o.textContent=s;input.append(o);}}
      else if(name==='Trade Date'){input.type='date';input.max=data.today;}
      else if(name==='Contract'){input.setAttribute('list',list.id);input.className='contract';input.placeholder='Search PMX DEC26…';}
      else if(['Price','Quantity','Fees'].includes(name)){input.inputMode='decimal';}
      else{input.placeholder='Optional';}
      input.value=values[name]??'';
      input.addEventListener('input',()=>saveRow(tr));input.addEventListener('change',()=>saveRow(tr));
      input.addEventListener('keydown',e=>{draft=read();if(!owns()||isLocked()){e.preventDefault();return;}draft.updated=Date.now();persist();
        if(e.key==='Enter'){e.preventDefault();const rows=[...body.rows],i=rows.indexOf(tr),j=names.indexOf(name);body.rows[i+1]?.cells[j]?.querySelector('input,select')?.focus();}
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
    const td=document.createElement('td');td.className='row-actions';tr.append(td);const undo=document.createElement('button');undo.textContent=history?'Delete':'×';undo.title=history?'Delete transaction':'Remove pending row';td.append(undo);
    undo.addEventListener('click',()=>{draft=read();if(!owns()||isLocked())return;
      if(history){if(!window.confirm('Delete this recorded transaction? P&L will be recalculated.'))return;const base=draft.edits[id]?.base||draft.deletes[id]||records.get(id);if(!base)return;draft.deletes[id]=base;delete draft.edits[id];tr.classList.add('deleted');}
      else{delete draft.edits[id];draft.blankRows=(draft.blankRows||[]).filter(k=>k!==id);entryRows=entryRows.filter(k=>k!==id);rowNodes.delete(id);tr.remove();if(!entryRows.length)addBlankRow();}
      change();
    });
    if(history){const cancel=document.createElement('button');cancel.textContent='Undo';cancel.title='Discard this row’s unsynced edit or deletion';td.append(cancel);
      cancel.addEventListener('click',()=>{draft=read();if(!owns()||isLocked())return;delete draft.edits[id];delete draft.deletes[id];change();renderAll();});}
    if(draft.deletes[id])tr.classList.add('deleted');
  }
  function addEntry(id=null,values=null){const identity=id||crypto.randomUUID();entryRows.push(identity);renderRow(identity,values||blank());}
  function addBlankRow(){const id=crypto.randomUUID();draft.blankRows=[...(draft.blankRows||[]),id];addEntry(id);persist();}
  function renderAll(){body.replaceChildren();rowNodes.clear();entryRows=[];
    if(data.mode==='entry'){
      for(const [id,e] of Object.entries(draft.edits)){if(!e.base)addEntry(id,e.fields);}
      for(const id of draft.blankRows||[]){if(!draft.edits[id])addEntry(id);}
      if(!entryRows.length)addBlankRow();
    }else{
      const ids=new Set([...data.visible,...Object.entries(draft.edits).filter(([id,e])=>e.base).map(([id])=>id),...Object.keys(draft.deletes)]);
      for(const id of ids){const t=records.get(id)||draft.edits[id]?.base||draft.deletes[id];if(t)renderRow(id,draft.edits[id]?.fields||raw(t),true);}
    }
    refreshStatus();
  }
  function refreshStatus(){
    const errors=issues();const blocked=new Set(errors.map(e=>e[0]));
    for(const [id,tr] of rowNodes){tr.querySelectorAll('input,select').forEach(input=>{input.classList.toggle('bad',blocked.has(id));input.disabled=isLocked()||!owns()||!!draft.deletes[id];});}
    addButton.disabled=isLocked()||!owns();
    saveButton.disabled=!pending()||!data.writable||errors.length>0||isLocked()||!owns();
    scroll.classList.toggle('locked',isLocked()||!owns());
    if(isLocked())status.textContent='Saving...';
    else if(!owns())status.textContent='Unsaved Changes · This browser draft is open in another tab';
    else if(pending())status.textContent=data.failure&&data.failure.version===draft.version?'Save failed · Unsaved Changes':'Unsaved Changes';
    else status.textContent=data.last_sync?'Saved · '+data.last_sync:data.mode==='entry'?'No pending trades':'No pending changes';
    error.textContent=storageFailed?'Browser storage is unavailable. Keep this tab open.':errors.length?errors[0][1]+' Batch not saved.':
      data.failure&&data.failure.version===draft.version?data.failure.message:pending()&&!data.writable?'Private write permission is not configured. The pending batch remains in this browser.':'';
  }
  renderAll();
  addButton.addEventListener('click',()=>{draft=read();if(!owns()||isLocked())return;addBlankRow();refreshStatus();});
  function submit(){
    draft=read();if(!owns()||isLocked()||!pending()||!data.writable||issues().length)return;
    locked=true;draft.savingUntil=Date.now()+180000;draft.submittedSession=data.session;draft.attempt=(draft.attempt||0)+1;persist();attempt=draft.attempt;
    notify();refreshStatus();
    setStateValue('batch',{scope:data.mode,version:draft.version,payload:payload(),attempt,submitted_at:Date.now()});
  }
  saveButton.addEventListener('click',submit);
  const external=e=>{if(e.detail&&e.detail!==storeKey)return;draft=read();if(data.mode==='history')refreshStatus();else refreshStatus();};
  const storage=e=>{if(e.key===storeKey){draft=read();renderAll();}};
  window.addEventListener('ffa-position-draft',external);window.addEventListener('storage',storage);
  let leaseTime=0;
  const timer=setInterval(()=>{
    draft=read();
    if(!owns()){refreshStatus();return;}
    if(draft.savingUntil&&draft.submittedSession!==data.session){
      locked=false;draft.savingUntil=0;draft.submittedSession=data.session;persist();
    }
    if(Date.now()-leaseTime>5000){draft.lease={owner:tab,until:Date.now()+15000};persist();leaseTime=Date.now();}
    if(data.failure&&data.failure.version===draft.version&&data.failure.attempt===draft.attempt&&draft.failedAttempt!==draft.attempt){
      locked=false;draft.savingUntil=0;draft.failedAttempt=draft.attempt;persist();
    }
    if(locked&&draft.savingUntil<=Date.now()){locked=false;draft.savingUntil=0;persist();}
    // Local lease/status maintenance only. A timer never sends a batch.
    refreshStatus();
  },500);
  return ()=>{clearInterval(timer);window.removeEventListener('ffa-position-draft',external);window.removeEventListener('storage',storage);};
}
'''

grid=component('ffa_position_batch_grid',html='<div class="wrap"></div>',css=CSS,js=JS)
