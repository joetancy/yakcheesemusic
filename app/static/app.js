async function addPlaylist(e){e.preventDefault();const el=document.getElementById('add-result');el.textContent='adding…';try{const url=document.getElementById('playlist-url').value;const name=document.getElementById('playlist-name').value;const r=await fetch('/api/playlists',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:url,name:name})});let j={};try{j=await r.json();}catch(_){}el.textContent=JSON.stringify(j);if(r.ok)location.reload();}catch(err){el.textContent='request failed: '+err;}return false;}
function toggleTheme(){const h=document.documentElement;const next=h.dataset.theme==='dark'?'light':'dark';h.dataset.theme=next;try{localStorage.setItem('theme',next);}catch(e){}}
async function trackJob(data, elId){
  const el=document.getElementById(elId);
  if(!el) return;
  if(!data || !data.ok){ el.textContent=JSON.stringify(data); return; }
  const id=data.job_id;
  el.textContent=`job ${id}: running…`;
  const timer=setInterval(async ()=>{
    try{
      const j=await (await fetch('/api/jobs/'+id)).json();
      let msg=`job ${id}: ${j.status} (seen ${j.tracks_seen||0}, done ${j.tracks_downloaded||0}, failed ${j.tracks_failed||0})`;
      if(j.error) msg+=` — ${j.error.slice(0,300)}`;
      el.textContent=msg;
      if(j.status!=='running') clearInterval(timer);
    }catch(e){ clearInterval(timer); el.textContent=`job ${id}: status unknown`; }
  },2000);
}
async function pollTrack(tid, el, timeoutMs){
  const end=Date.now()+(timeoutMs||120000);
  for(;;){
    let r;
    try{ r=await fetch('/api/tracks/'+tid); }
    catch(e){ throw new Error('request failed: '+e); }
    if(!r.ok) throw new Error('track status '+r.status);
    const j=await r.json();
    if(j.status!=='searching'){ if(el) el.textContent='search '+j.status; return j.status; }
    if(el) el.textContent='searching…';
    if(Date.now()>end) throw new Error('search timed out');
    await new Promise(res=>setTimeout(res,2000));
  }
}
function makeSortable(table){
  if(!table || !table.tHead) return;
  const ths=[...table.tHead.rows[0].cells];
  ths.forEach((th,i)=>{
    th.style.cursor='pointer'; th.title='Sort';
    th.addEventListener('click',()=>{
      const dir=th.dataset.dir==='asc'?'desc':'asc';
      ths.forEach(h=>{delete h.dataset.dir; h.textContent=h.textContent.replace(/ [▲▼]$/,'');});
      th.dataset.dir=dir;
      th.textContent+=dir==='asc'?' ▲':' ▼';
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
  });
}
