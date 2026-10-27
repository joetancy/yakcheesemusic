function setFeedback(el, message, state='info'){
  if(!el) return;
  el.textContent=message;
  el.dataset.state=state;
}
async function readResponse(response){
  let body={};
  try{body=await response.json();}catch(_){}
  if(!response.ok){
    const detail=body.detail||body.error||body.message;
    throw new Error(typeof detail==='string'?detail:`Request failed (${response.status}). Please try again.`);
  }
  return body;
}
function setBusy(button, busy, label='Working…'){
  if(!button) return;
  if(busy){
    if(!button.disabled) button.dataset.originalLabel=button.textContent;
    button.disabled=true;
    button.setAttribute('aria-busy','true');
    button.textContent=label;
  }else{
    button.disabled=false;
    button.removeAttribute('aria-busy');
    if(button.dataset.originalLabel){button.textContent=button.dataset.originalLabel;delete button.dataset.originalLabel;}
  }
}
async function addPlaylist(e){
  e.preventDefault();
  const form=e.currentTarget, button=form.querySelector('[type="submit"]');
  const el=document.getElementById('add-result');
  setFeedback(el,'Adding playlist…','loading');setBusy(button,true,'Adding…');
  try{
    await readResponse(await fetch('/api/playlists',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:form.elements.url.value,name:form.elements.name.value})}));
    setFeedback(el,'Playlist added. Loading your playlists…','success');
    setTimeout(()=>location.reload(),500);
  }catch(err){
    setFeedback(el,`Could not add playlist: ${err.message}`,'error');setBusy(button,false);
  }
  return false;
}
function updateThemeControl(){
  const dark=document.documentElement.dataset.theme==='dark'||
    (!document.documentElement.dataset.theme&&matchMedia('(prefers-color-scheme: dark)').matches);
  const button=document.getElementById('theme-toggle');
  if(!button) return;
  const label=dark?'Switch to light theme':'Switch to dark theme';
  button.setAttribute('aria-label',label);
  button.title=label;
  button.querySelector('use').setAttribute('href',`/static/icons.svg#${dark?'sun':'moon'}`);
}
function toggleTheme(){const h=document.documentElement;const current=h.dataset.theme||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');const next=current==='dark'?'light':'dark';h.dataset.theme=next;h.classList.toggle('dark',next==='dark');try{localStorage.setItem('theme',next);}catch(e){}updateThemeControl();}
document.addEventListener('DOMContentLoaded',updateThemeControl,{once:true});
async function trackJob(data, elId, onDone, button){
  const el=document.getElementById(elId);
  if(!el) return;
  if(!data || !data.ok){ setFeedback(el,data?.detail||'Could not start the job. Please try again.','error');setBusy(button,false);return; }
  const id=data.job_id;
  setFeedback(el,`Job ${id} is running…`,'loading');
  const timer=setInterval(async ()=>{
    try{
      const r=await fetch('/api/jobs/'+id);
      const j=await readResponse(r);
      let msg=`job ${id}: ${j.status} (seen ${j.tracks_seen||0}, done ${j.tracks_downloaded||0}, failed ${j.tracks_failed||0})`;
      if(j.error) msg+=` — ${j.error.slice(0,300)}`;
      setFeedback(el,msg,['failed','error'].includes(j.status)?'error':j.status==='running'?'loading':'success');
      if(j.status!=='running'){ clearInterval(timer);setBusy(button,false);if(onDone) onDone(j); }
    }catch(e){ clearInterval(timer);setBusy(button,false);setFeedback(el,`Could not check job ${id}. Refresh the page to check its status.`,'error'); }
  },2000);
}
async function pollTrack(tid, el, timeoutMs){
  const end=Date.now()+(timeoutMs||120000);
  for(;;){
    let r;
    try{ r=await fetch('/api/tracks/'+tid); }
    catch(e){ throw new Error('request failed: '+e); }
    if(!r.ok) throw new Error('Could not check track status. Please try again.');
    const j=await r.json();
    if(j.status!=='searching'){ if(el) setFeedback(el,`Search ${j.status}.`,'success'); return j.status; }
    if(el) setFeedback(el,'Searching…','loading');
    if(Date.now()>end) throw new Error('search timed out');
    await new Promise(res=>setTimeout(res,2000));
  }
}
document.addEventListener('DOMContentLoaded',function rememberSections(){
  try{
    document.querySelectorAll('details[data-remember]').forEach(d=>{
      const k='section:'+d.dataset.remember;
      if(localStorage.getItem(k)==='closed') d.removeAttribute('open');
      d.addEventListener('toggle',()=>{
        try{localStorage.setItem(k, d.open?'open':'closed');}catch(e){}
      });
    });
  }catch(e){}
},{once:true});
(function clickableRows(){
  document.addEventListener('click', e=>{
    const row=e.target.closest('tr[data-href]');
    if(!row || e.target.closest('a,button,input,select,textarea')) return;
    location.href=row.dataset.href;
  });
  document.addEventListener('keydown', e=>{
    const row=e.target.closest('tr[data-href]');
    if(row && !e.target.closest('a,button,input,select,textarea') &&
       (e.key==='Enter' || e.key===' ')){
      e.preventDefault();
      location.href=row.dataset.href;
    }
  });
})();
function makeSortable(table){
  if(!table || !table.tHead) return;
  const ths=[...table.tHead.rows[0].cells];
  ths.forEach((th,i)=>{
    th.tabIndex=0;th.title=`Sort by ${th.textContent.trim()}`;th.setAttribute('aria-sort','none');
    th.addEventListener('click',()=>{
      const dir=th.dataset.dir==='asc'?'desc':'asc';
      ths.forEach(h=>{delete h.dataset.dir;h.setAttribute('aria-sort','none');h.querySelector('.sort-indicator')?.remove();});
      th.dataset.dir=dir;
      th.setAttribute('aria-sort',dir==='asc'?'ascending':'descending');
      const indicator=document.createElement('span');indicator.className='sort-indicator';indicator.setAttribute('aria-hidden','true');indicator.textContent=dir==='asc'?' ▲':' ▼';th.append(indicator);
      const num=th.dataset.sort==='num';
      const body=table.tBodies[0];
      const rows=[...body.rows].filter(r=>r.cells.length===ths.length);
      const rest=[...body.rows].filter(r=>r.cells.length!==ths.length);
      rows.sort((a,b)=>{
        const x=a.cells[i].textContent.trim(), y=b.cells[i].textContent.trim();
        if(num) return (parseFloat(x)||0)-(parseFloat(y)||0);
        return x.localeCompare(y,undefined,{numeric:true,sensitivity:'base'});
      });
      if(dir==='desc') rows.reverse();
      rows.forEach(r=>body.appendChild(r));
      rest.forEach(r=>body.appendChild(r));
    });
    th.addEventListener('keydown',e=>{
      if(e.key==='Enter'||e.key===' '){e.preventDefault();th.click();}
    });
  });
}
