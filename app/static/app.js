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
