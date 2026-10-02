const state={language:"yoruba",target:30,provider:"youtube",movies:[],downloadMovies:[],runtime:null,counts:{},mode:"discover",selection:new Set(),downloadableOnly:false,downloadTab:"downloading",moviesPage:0,moviesPageSize:24,moviesTotal:0,downloadPages:{downloading:0,queued:0,completed:0,failed:0},logLastId:0};
const $=id=>document.getElementById(id);const els={};
function cache(){["languageSelect","targetInput","modeSelect","primaryActionBtn","secondaryStopBtn","discoverySetup","statTarget","statAccepted","statDownloadable","statDownloaded","statRemaining","statSourceMissing","statSourceInvalid","statFound","statRejected","discoveryStatus","downloadStatus","discoveryBar","discoveryProgressText","discoveryPercent","scannedCount","underCount","notMovieCount","wrongLanguageCount","duplicateCount","apiCount","retryCount","currentQuery","discoveryMessage","recentMovies","allMovies","activityLog","downloadSummary","movieSearch","movieStatusFilter","movieSort","refreshMovies","moviesPrevBtn","moviesNextBtn","moviesPageInfo","moviesPageNumbers","librarySummary","downloadPrimaryBtn","downloadStopBtn","retryFailedBtn","minimumDuration","moviesPerPage","maxConcurrentDownloads","minFreeDiskGb","downloadRoot","chooseFolder","saveSettings","settingsSaveStatus","apiConfigured","movieModal","modalBody","toastWrap","networkDot","networkText","clearActivity","moviesTitle","selectPageBtn","selectAllFilteredBtn","clearSelectionBtn","bulkCount","bulkActions","bulkRemoveReplace","bulkWrongLanguage","bulkRestore","clearLibraryBtn","startFreshBtn","dashboardStateBanner","downloadStateBanner","downloadsPageStateBanner","sessionOutcome","heroProgressTitle","heroProgressSub","heroProgressCount","heroProgressPct","heroProgressFill","heroStatusText","activeJobsGrid","dgDownloading","dgDownloadingCount","dgQueued","dgQueuedCount","dgCompleted","dgCompletedCount","dgFailed","dgFailedCount","tabDownloadingCount","tabQueuedCount","tabCompletedCount","tabFailedCount","downloadsPrevBtn","downloadsNextBtn","downloadsPageNumbers","downloadsPageInfo","downloadPagination","bulkBar","dashActiveCount","dashQueuedCount","dashCompletedCount","dashFailedCount","dashDownloadPrimaryBtn","dashDownloadStopBtn","youtubeUseBrowserSession","youtubeBrowser"].forEach(id=>els[id]=$(id));}
// New YouTube-only controls are intentionally kept outside the legacy cache list.
els.downloadQuality=$("downloadQuality");els.bulkDownloadSelected=$("bulkDownloadSelected");els.logDiagnostics=$("logDiagnostics");
async function api(url,options={}){const opts={headers:{"Content-Type":"application/json"},...options};const r=await fetch(url,opts);let b={};try{b=await r.json()}catch{}if(!r.ok||b.ok===false)throw new Error(b.error||`Request failed (${r.status})`);return b}
function esc(v=""){return String(v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]))}
function toast(m,t=""){const n=document.createElement("div");n.className=`toast ${t}`;n.textContent=m;els.toastWrap.appendChild(n);setTimeout(()=>n.remove(),4200)}
// ---- Live Log ---------------------------------------------------------------
// One chronological log for discovery + downloads. The backend writes every line
// (and keeps the recent ones), so a reloaded page shows the same history.
const LOG_MAX=500;
const LOG_LEVEL_CLASS={good:"log-good",bad:"log-bad",warn:"log-warn",dim:"log-dim"};
function logTime(at){let d=at?new Date(at):new Date();if(isNaN(d))d=new Date();return d.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false})}
function appendLogLine(entry){
  const box=els.activityLog;if(!box||!entry||!entry.message)return;
  if(entry.id){if(entry.id<=state.logLastId)return;state.logLastId=entry.id}
  const last=box.lastChild;
  if(last&&last._msg===entry.message)return; // never repeat an identical line back to back
  const follow=!(box.scrollHeight>0)||box.scrollHeight-box.scrollTop-box.clientHeight<32;
  const n=document.createElement("div");n._msg=entry.message;
  n.className=`log-line ${LOG_LEVEL_CLASS[entry.level]||""}`.trim();
  n.innerHTML=`<span class="log-time">${logTime(entry.at)}</span><span class="log-text">${esc(entry.message)}</span>`;
  box.appendChild(n);
  while(box.children.length>LOG_MAX)box.firstChild.remove();
  if(follow)box.scrollTop=box.scrollHeight;
}
function addLog(m,k=""){appendLogLine({message:m,level:k==="log-good"?"good":k==="log-bad"?"bad":"info"})}
function pushLogEntry(p){if(p&&p.language&&state.language&&p.language!==state.language)return;appendLogLine(p)}
async function syncLog(reset){
  if(!els.activityLog)return;
  try{
    if(reset){els.activityLog.innerHTML="";state.logLastId=0}
    const r=await api(`/api/log?language=${encodeURIComponent(state.language)}&after=${state.logLastId}&limit=300`);
    (r.entries||[]).forEach(appendLogLine);
  }catch{}
}
function renderLogDiagnostics(d){
  if(!els.logDiagnostics)return;
  const s=d?.stats||{},langLabel=state.language?state.language[0].toUpperCase()+state.language.slice(1):"";
  const rejected=Number(s.rejected_under_duration||0)+Number(s.rejected_not_movie||0)+Number(s.rejected_wrong_language||0);
  const parts=[`${langLabel} discovery: ${runtimeStatusLabel(d?.status||"IDLE")}`];
  if(Number(s.api_requests||0)||Number(s.candidates_scanned||0)){
    parts.push(`${Number(s.candidates_scanned||0).toLocaleString()} scanned`,`${Number(s.duplicates_skipped||0).toLocaleString()} duplicates`,`${rejected.toLocaleString()} rejected`,`${Number(s.api_requests||0).toLocaleString()} YouTube requests`);
  }
  if(d?.current_query&&["RUNNING","PAUSED"].includes(d.status))parts.push(`now: ${d.current_query}`);
  els.logDiagnostics.textContent=parts.join(" • ");
}
// A progress bar must never read 100% before the target is really reached.
function progressPercent(progress,target){if(!target||target<=0)return 0;if(progress>=target)return 100;return Math.min(99.9,Math.floor(progress/target*1000)/10)}
// Another language's worker state is never shown as this language's state.
function discoveryFor(rt){const d=rt?.discovery||{};return(!d.language||!state.language||d.language===state.language)?d:{status:"IDLE",stats:{}}}
function downloadsFor(rt){const dl=rt?.downloads||{};return(!dl.language||!state.language||dl.language===state.language)?dl:{status:"IDLE",active_jobs:[],active_count:0}}
let refreshTimer=null,refreshWithMovies=false;
function scheduleRefresh(withMovies){refreshWithMovies=refreshWithMovies||!!withMovies;if(refreshTimer)return;refreshTimer=setTimeout(async()=>{refreshTimer=null;const m=refreshWithMovies;refreshWithMovies=false;await refresh();if(m)await loadMovies().catch(()=>{})},500)}
function fmtBytes(n){if(n==null)return"—";let v=Number(n),u=["B","KB","MB","GB","TB"],i=0;while(v>=1024&&i<u.length-1){v/=1024;i++}return`${v.toFixed(i?1:0)} ${u[i]}`}
function fmtTime(s){if(s==null||!isFinite(s))return"—";s=Math.max(0,Math.round(s));if(s<60)return`${s}s`;const m=Math.floor(s/60),x=s%60;if(m<60)return`${m}m ${x}s`;return`${Math.floor(m/60)}h ${m%60}m`}
const PHASE_PRIORITY=["error","disk-low","waiting-network","running","paused","stopping","continue","completed","waiting-source","idle"];
const DASHBOARD_PHASE_LABELS={
  idle:{label:"Find movies",cls:"primary",disabled:false},
  running:{label:"Pause finding",cls:"primary",disabled:false},
  "waiting-network":{label:"Waiting for internet…",cls:"",disabled:true},
  "disk-low":{label:"Low disk space",cls:"",disabled:true},
  paused:{label:"Resume finding",cls:"primary",disabled:false},
  stopping:{label:"Stopping…",cls:"",disabled:true},
  completed:{label:"✓ Completed",cls:"",disabled:true},
  continue:{label:"Find more movies",cls:"primary",disabled:false},
  error:{label:"Try discovery again",cls:"primary",disabled:false},
  "waiting-source":{label:"No downloads queued",cls:"",disabled:true},
};
const DOWNLOAD_PHASE_LABELS={
  idle:{label:"Start downloads",cls:"primary",disabled:false},
  running:{label:"Pause",cls:"primary",disabled:false},
  "waiting-network":{label:"Waiting for internet…",cls:"",disabled:true},
  "disk-low":{label:"Low disk space",cls:"",disabled:true},
  paused:{label:"Resume",cls:"primary",disabled:false},
  stopping:{label:"Stopping…",cls:"",disabled:true},
  error:{label:"Retry",cls:"primary",disabled:false},
  "waiting-source":{label:"No downloads queued",cls:"",disabled:true},
};
// Same phases as DOWNLOAD_PHASE_LABELS, worded so the Dashboard's download
// controls are never mistaken for the discovery Start/Pause/Stop controls
// sitting right above them.
const DASH_DOWNLOAD_PHASE_LABELS={
  idle:{label:"Start downloads",cls:"primary",disabled:false},
  running:{label:"Pause downloads",cls:"primary",disabled:false},
  "waiting-network":{label:"Waiting for internet…",cls:"",disabled:true},
  "disk-low":{label:"Low disk space",cls:"",disabled:true},
  paused:{label:"Resume downloads",cls:"primary",disabled:false},
  stopping:{label:"Stopping…",cls:"",disabled:true},
  error:{label:"Retry downloads",cls:"primary",disabled:false},
  "waiting-source":{label:"No downloads queued",cls:"",disabled:true},
};
function deriveDiscoveryPhase(){
  const d=discoveryFor(state.runtime),status=d.status||"IDLE";
  const target=Math.max(1,Number(els.targetInput?.value||state.target||0));
  const progress=state.downloadableOnly?Number(state.counts.downloadable||0):Number(state.counts.accepted||0);
  if(status==="ERROR")return"error";
  if(status==="RUNNING")return d.network_wait?"waiting-network":"running";
  if(status==="PAUSED")return"paused";
  if(status==="STOPPING")return"stopping";
  if(status==="COMPLETED"||status==="EXHAUSTED")return target>progress?"continue":"completed";
  return"idle"
}
function deriveDownloadPhase(){
  const dl=downloadsFor(state.runtime),status=dl.status||"IDLE";
  if(status==="ERROR")return"error";
  if(status==="DISK_LOW")return"disk-low";
  if(status==="RUNNING")return dl.network_wait?"waiting-network":"running";
  if(status==="PAUSED")return"paused";
  if(status==="STOPPING")return"stopping";
  if(status==="WAITING")return"waiting-source";
  return"idle"
}
function combineDashboardPhase(){
  const mode=els.modeSelect?.value||state.mode||"discover";
  if(mode==="discover")return deriveDiscoveryPhase();
  if(mode==="download")return deriveDownloadPhase();
  const dPhase=deriveDiscoveryPhase(),dlPhase=deriveDownloadPhase();
  return PHASE_PRIORITY.find(p=>p===dPhase||p===dlPhase)||"idle"
}
function applyButtonPhase(btn,info){btn.textContent=info.label;btn.disabled=!!info.disabled;btn.classList.toggle("primary",info.cls==="primary")}
function renderDashboardControls(){
  const phase=combineDashboardPhase(),info=DASHBOARD_PHASE_LABELS[phase]||DASHBOARD_PHASE_LABELS.idle;
  applyButtonPhase(els.primaryActionBtn,info);
  if(phase==="completed")els.primaryActionBtn.textContent="Movies found";
  if(els.discoverySetup)els.discoverySetup.classList.toggle("hidden",phase==="completed");
  const showStop=["running","waiting-network","disk-low","paused","stopping","waiting-source"].includes(phase);
  els.secondaryStopBtn.classList.toggle("hidden",!showStop);
  els.secondaryStopBtn.disabled=phase==="stopping";
  const locked=["running","paused","waiting-network","disk-low","stopping"].includes(phase);
  els.languageSelect.disabled=locked||["RUNNING","PAUSED","STOPPING","DISK_LOW"].includes(downloadsFor(state.runtime).status);
  els.modeSelect.disabled=locked;
  els.targetInput.disabled=phase==="running"||phase==="waiting-network"||phase==="stopping";
}
function renderDownloadControls(){
  const phase=deriveDownloadPhase(),info=DOWNLOAD_PHASE_LABELS[phase]||DOWNLOAD_PHASE_LABELS.idle;
  applyButtonPhase(els.downloadPrimaryBtn,info);
  const showStop=["running","waiting-network","disk-low","paused","stopping","waiting-source"].includes(phase);
  els.downloadStopBtn.classList.toggle("hidden",!showStop);
  els.downloadStopBtn.disabled=phase==="stopping";
  if(els.dashDownloadPrimaryBtn){
    const dashInfo=DASH_DOWNLOAD_PHASE_LABELS[phase]||DASH_DOWNLOAD_PHASE_LABELS.idle;
    applyButtonPhase(els.dashDownloadPrimaryBtn,dashInfo);
    els.dashDownloadStopBtn.classList.toggle("hidden",!showStop);
    els.dashDownloadStopBtn.disabled=phase==="stopping";
  }
}
async function dashboardPrimaryAction(){
  const mode=els.modeSelect.value,phase=combineDashboardPhase(),dPhase=deriveDiscoveryPhase(),dlPhase=deriveDownloadPhase();
  const target=Math.max(1,Number(els.targetInput.value||state.target||30));
  try{
    if(phase==="idle"){await startAction();return}
    if(phase==="continue"){
      state.target=target;
      await api("/api/discovery/start",{method:"POST",body:JSON.stringify({language:state.language,target,provider:"youtube"})});
      await refresh();return
    }
    if(phase==="error"){
      const tasks=[];
      if((mode==="discover"||mode==="both")&&dPhase==="error")tasks.push(api("/api/discovery/start",{method:"POST",body:JSON.stringify({language:state.language,target,provider:"youtube"})}));
      if((mode==="download"||mode==="both")&&dlPhase==="error")tasks.push(api("/api/downloads/start",{method:"POST",body:JSON.stringify({language:state.language})}));
      await Promise.all(tasks);
      await refresh();return
    }
    if(phase==="running"){
      const tasks=[];
      if(mode==="discover"||mode==="both")tasks.push(api("/api/discovery/pause",{method:"POST"}));
      if(mode==="download"||mode==="both")tasks.push(api("/api/downloads/pause",{method:"POST"}));
      await Promise.all(tasks);await refresh();return
    }
    if(phase==="paused"){
      const tasks=[];
      if(mode==="discover"||mode==="both"){
        state.target=target;
        tasks.push(api("/api/discovery/start",{method:"POST",body:JSON.stringify({language:state.language,target,provider:"youtube"})}));
      }
      if(mode==="download"||mode==="both")tasks.push(api("/api/downloads/resume",{method:"POST"}));
      await Promise.all(tasks);
      await refresh();return
    }
  }catch(e){toast(e.message,"error")}
}
async function dashboardStopAction(){
  const mode=els.modeSelect.value;
  try{
    const tasks=[];
    if(mode==="discover"||mode==="both")tasks.push(api("/api/discovery/stop",{method:"POST"}));
    if(mode==="download"||mode==="both")tasks.push(api("/api/downloads/stop",{method:"POST"}));
    await Promise.all(tasks);await refresh()
  }catch(e){toast(e.message,"error")}
}
async function downloadPrimaryAction(){
  const phase=deriveDownloadPhase();
  try{
    if(phase==="idle"||phase==="error"){await api("/api/downloads/start",{method:"POST",body:JSON.stringify({language:state.language})})}
    else if(phase==="running"){await api("/api/downloads/pause",{method:"POST"})}
    else if(phase==="paused"){await api("/api/downloads/resume",{method:"POST"})}
    await refresh()
  }catch(e){toast(e.message,"error")}
}
async function downloadStopAction(){try{await api("/api/downloads/stop",{method:"POST"});await refresh()}catch(e){toast(e.message,"error")}}
function renderLanguages(l){els.languageSelect.innerHTML="";Object.entries(l).forEach(([k,p])=>{const o=document.createElement("option");o.value=k;o.textContent=p.enabled?p.label:`${p.label} — later`;o.disabled=!p.enabled;els.languageSelect.appendChild(o)});els.languageSelect.value=state.language}
const RUNTIME_STATUS_LABELS={IDLE:"Idle",RUNNING:"Running",PAUSED:"Paused",STOPPING:"Stopping…",STOPPED:"Stopped",COMPLETED:"Completed",EXHAUSTED:"Search pool exhausted",ERROR:"Needs attention",WAITING:"Waiting for downloadable movies",DISK_LOW:"Paused — low disk space"};
function runtimeStatusLabel(status){return RUNTIME_STATUS_LABELS[status]||status||"Idle"}
const JOB_STAGE_LABELS={QUEUED:"Queued",PREPARING:"Preparing",DOWNLOADING:"Downloading",MERGING:"Combining video and audio",VERIFYING:"Checking movie",FINALISING:"Finishing movie",RETRY_WAIT:"Trying again shortly"};
function jobStageLabel(stage){return JOB_STAGE_LABELS[stage]||"Downloading"}
const MOVIE_STATUS_LABELS={DISCOVERED:"Found",ACCEPTED:"Accepted",QUEUED:"Queued",DOWNLOADING:"Downloading",DOWNLOADED:"Downloaded",REJECTED:"Removed"};
function movieStatusLabel(status){return MOVIE_STATUS_LABELS[status]||status||""}
const DOWNLOAD_STATUS_LABELS={NOT_READY:"Not ready yet",READY:"Ready to download",QUEUED:"Queued",DOWNLOADING:"Downloading",WAITING_NETWORK:"Waiting for internet",DOWNLOADED:"Completed",FAILED:"Could not download"};
function downloadStatusLabel(status){return DOWNLOAD_STATUS_LABELS[status]||"Not ready yet"}
function statusPillClass(status){if(["ERROR","EXHAUSTED"].includes(status))return"warn";if(["RUNNING","COMPLETED"].includes(status))return"ok";if(["DISK_LOW"].includes(status))return"warn";return"neutral"}
function applyStatusPill(el,status){el.textContent=runtimeStatusLabel(status);el.className=`status-pill ${statusPillClass(status)}`}
function renderStateBanner(el,status,messageOverride){
  const map={
    WAITING_NETWORK:{cls:"warn",icon:"📶",title:"Waiting for internet",body:"Your downloads will continue automatically when the connection returns."},
    DISK_LOW:{cls:"warn",icon:"💾",title:"Downloads paused — low disk space",body:"Free some space on the download drive. Movie Manager will continue afterwards."},
    ERROR:{cls:"error",icon:"⚠️",title:"Needs attention",body:messageOverride||"Something went wrong. You can retry from here."},
  };
  const info=map[status];
  if(!info){el.classList.add("hidden");el.innerHTML="";return}
  el.classList.remove("hidden");
  el.className=`state-banner ${info.cls}`;
  el.innerHTML=`<span class="icon" aria-hidden="true">${info.icon}</span><div><strong>${esc(info.title)}</strong><p>${esc(messageOverride||info.body)}</p></div>`;
}
function bannerStatusFor(rt){
  const d=discoveryFor(rt),dl=downloadsFor(rt);
  if(dl.status==="DISK_LOW")return{status:"DISK_LOW"};
  if(dl.network_wait||d.network_wait)return{status:"WAITING_NETWORK"};
  if(d.status==="ERROR")return{status:"ERROR",message:d.message};
  if(dl.status==="ERROR")return{status:"ERROR",message:dl.message};
  return{status:null};
}
function emptyStateHtml(icon,title,body){return`<div class="empty-state" style="grid-column:1/-1"><div class="icon" aria-hidden="true">${icon}</div><strong>${esc(title)}</strong><p>${esc(body)}</p></div>`}
function jobThumb(job){const m=state.movies.find(x=>Number(x.id)===Number(job.movie_id));return m&&m.thumbnail_url?`<img loading="lazy" src="${esc(m.thumbnail_url)}" alt="">`:"🎬"}
function renderActiveJobs(jobs){
  jobs=jobs||[];
  if(!jobs.length){els.activeJobsGrid.innerHTML="";return}
  els.activeJobsGrid.innerHTML=jobs.map(j=>{
    const pct=j.total_bytes?Math.min(100,(j.bytes_downloaded||0)/j.total_bytes*100):null;
    const pctLabel=pct==null?"":`${pct.toFixed(0)}%`;
    return `<article class="active-job-card glass">
      <div class="active-job-thumb">${jobThumb(j)}</div>
      <div class="active-job-body">
        <p class="active-job-title" title="${esc(j.title||"")}">${esc(j.title||"Movie")}</p>
        <p class="active-job-stage">${esc(jobStageLabel(j.stage))}${pctLabel?" • "+pctLabel:""}</p>
        <div class="progress-track small"><div class="progress-fill" style="width:${pct==null?0:pct}%"></div></div>
        <div class="active-job-meta"><span>${fmtBytes(j.bytes_downloaded)}${j.total_bytes?" / "+fmtBytes(j.total_bytes):""}</span><span>${j.speed_bps?fmtBytes(j.speed_bps)+"/s":""}</span></div>
        <div class="active-job-meta"><span>${j.eta_seconds!=null?"ETA "+fmtTime(j.eta_seconds):""}</span><span>${j.retries?`Retry ${j.retries}`:""}</span></div>
      </div>
    </article>`;
  }).join("");
}
function downloadCardHtml(m,kind){
  const thumb=m.thumbnail_url?`<img loading="lazy" src="${esc(m.thumbnail_url)}" alt="">`:"";
  let sub="",bytes,total,speed,eta,pct=null;
  if(kind==="downloading"){
    // Live numbers come from the active job (kept fresh by download_progress/
    // download_stage), not the movie row -- that only reflects the last
    // loadMovies() snapshot and would otherwise show stale bytes/stage.
    const j=m._job||{};
    bytes=j.bytes_downloaded!=null?j.bytes_downloaded:m.bytes_downloaded;
    total=j.total_bytes!=null?j.total_bytes:m.total_bytes;
    speed=j.speed_bps!=null?j.speed_bps:m.download_speed_bps;
    eta=j.eta_seconds!=null?j.eta_seconds:m.download_eta_seconds;
    const stage=j.stage||m._stage||"DOWNLOADING";
    pct=total?Math.min(100,(bytes||0)/total*100):null;
    sub=`${jobStageLabel(stage)}${pct!=null?` • ${pct.toFixed(0)}%`:""}${speed?` • ${fmtBytes(speed)}/s`:""}${eta!=null?` • ETA ${fmtTime(eta)}`:""}`;
  }else if(kind==="queued"){
    sub="Waiting for a free download slot";
  }else if(kind==="completed"){
    sub="Ready to watch in your movie library";
  }else if(kind==="failed"){
    sub=m.download_error?`Could not download — ${m.download_error}`:"Could not download";
  }
  if(kind==="failed")sub=friendlyDownloadError(m.download_error);
  const status=kind==="downloading"?(pct!=null?`Downloading ${pct.toFixed(0)}%`:"Downloading"):kind==="queued"?"Waiting":kind==="completed"?"Completed":"Needs attention";
  const progress=kind==="downloading"?`<div class="download-card-progress"><i style="width:${pct||0}%"></i></div><div class="download-card-meta"><span>${total?`${fmtBytes(bytes)} / ${fmtBytes(total)}`:"Preparing file"}</span><span>${speed?`${fmtBytes(speed)}/s`:eta!=null?`ETA ${fmtTime(eta)}`:""}</span></div>`:"";
  const action=kind==="failed"?"Resolve issue":kind==="completed"?"Open movie":"View details";
  return `<article class="download-card ${kind}" data-open="${m.id}"><div class="download-card-thumb">${thumb}<span class="download-card-status">${status}</span></div><div class="download-card-body"><div class="download-card-title" title="${esc(m.title)}">${esc(m.title)}</div><div class="download-card-sub">${esc(sub)}</div>${progress}<button class="mini-btn download-card-action" data-open="${m.id}">${action}</button></div></article>`;
}
function friendlyDownloadError(error=""){
  const message=String(error);
  if(/private video/i.test(message))return"This video is private. Replace it with another movie.";
  if(/temporarily (blocked|limited)|not a bot|page needs to be reloaded/i.test(message))return"YouTube needs a short break. Movie Manager will retry when it is safe.";
  if(/sign in/i.test(message))return"YouTube needs extra access for this video. Open the movie for options.";
  return message?"This movie could not download. Open it to retry or replace it.":"This movie could not download. Open it to resolve the issue.";
}
function setDownloadTab(tab){
  state.downloadTab=tab;
  state.downloadPages[tab]=0;
  document.querySelectorAll("[data-download-tab]").forEach(b=>{const active=b.dataset.downloadTab===tab;b.classList.toggle("active",active);b.setAttribute("aria-selected",String(active))});
  document.querySelectorAll("[data-download-group]").forEach(g=>g.classList.toggle("hidden",g.dataset.downloadGroup!==tab));
}
function renderDownloadPageNumbers(totalPages,current){
  els.downloadsPageNumbers.innerHTML=Array.from({length:totalPages},(_,i)=>`<button class="page-number ${i===current?"active":""}" data-download-page="${i}" aria-label="Download page ${i+1}" ${i===current?"aria-current=\"page\"":""}>${i+1}</button>`).join("");
}
function renderDownloadsPage(rows){
  const activeJobs=downloadsFor(state.runtime).active_jobs||[];
  const jobByMovie={};
  activeJobs.forEach(j=>{jobByMovie[Number(j.movie_id)]=j});
  const activeIds=new Set(activeJobs.map(j=>Number(j.movie_id)));
  const downloading=[],queued=[],completed=[],failed=[];
  rows.forEach(m=>{
    const id=Number(m.id);
    if(activeIds.has(id)){m._job=jobByMovie[id];m._stage=m._job&&m._job.stage;downloading.push(m)}
    else if(m.download_status==="DOWNLOADED")completed.push(m);
    else if(m.download_status==="FAILED")failed.push(m);
    else if(["READY","WAITING_NETWORK","QUEUED"].includes(m.download_status))queued.push(m);
  });
  els.dgDownloadingCount.textContent=downloading.length;
  els.dgQueuedCount.textContent=queued.length;
  els.dgCompletedCount.textContent=completed.length;
  els.dgFailedCount.textContent=failed.length;
  els.tabDownloadingCount.textContent=downloading.length;
  els.tabQueuedCount.textContent=queued.length;
  els.tabCompletedCount.textContent=completed.length;
  els.tabFailedCount.textContent=failed.length;
  const groups={downloading,queued,completed,failed},active=groups[state.downloadTab]||[];
  const pageSize=30,totalPages=Math.max(1,Math.ceil(active.length/pageSize));
  if(state.downloadPages[state.downloadTab]>=totalPages)state.downloadPages[state.downloadTab]=totalPages-1;
  const page=state.downloadPages[state.downloadTab],visible=active.slice(page*pageSize,page*pageSize+pageSize);
  const renderGroup=(name,icon,title,body)=>{const list=groups[name],el=els[`dg${name[0].toUpperCase()+name.slice(1)}`];el.innerHTML=name===state.downloadTab?(list.length?visible.map(m=>downloadCardHtml(m,name)).join(""):emptyStateHtml(icon,title,body)):""};
  renderGroup("downloading","⬇️","Nothing downloading right now","Press Start downloads to begin.");
  renderGroup("queued","🕓","No downloads queued","Movies ready to download will appear here.");
  renderGroup("completed","✅","No completed movies yet","Finished movies will appear here automatically.");
  renderGroup("failed","✔️","No failed downloads","Movie Manager will show anything it couldn't download here.");
  els.downloadPagination?.classList.toggle("hidden",active.length<=pageSize);
  if(els.downloadsPrevBtn)els.downloadsPrevBtn.disabled=page===0;if(els.downloadsNextBtn)els.downloadsNextBtn.disabled=page>=totalPages-1;if(els.downloadsPageInfo)els.downloadsPageInfo.textContent=`Page ${page+1} of ${totalPages}`;if(els.downloadsPageNumbers)renderDownloadPageNumbers(totalPages,page);
}
function renderStats(rt){const target=Number(state.target||0),accepted=Number(state.counts.accepted||0),downloadable=Number(state.counts.downloadable||0),downloaded=Number(state.counts.downloaded||0),sourceMissing=Number(state.counts.source_missing||0),sourceInvalid=Number(state.counts.source_invalid||0),found=Number(state.counts.all||0),rejected=Number(state.counts.rejected||0),progress=state.downloadableOnly?downloadable:accepted,rem=Math.max(target-progress,0);els.statTarget.textContent=target.toLocaleString();els.statAccepted.textContent=accepted.toLocaleString();els.statDownloadable.textContent=downloadable.toLocaleString();els.statDownloaded.textContent=downloaded.toLocaleString();els.statRemaining.textContent=rem.toLocaleString();els.statSourceMissing.textContent=sourceMissing.toLocaleString();els.statSourceInvalid.textContent=sourceInvalid.toLocaleString();els.statFound.textContent=found.toLocaleString();els.statRejected.textContent=rejected.toLocaleString();const pct=progressPercent(progress,target);els.discoveryBar.style.width=`${pct}%`;els.discoveryProgressText.textContent=`${progress.toLocaleString()} / ${target.toLocaleString()}`;els.discoveryPercent.textContent=`${pct.toFixed(1)}%`;const d=discoveryFor(rt),s=d.stats||{};applyStatusPill(els.discoveryStatus,d.status||"IDLE");els.scannedCount.textContent=Number(s.candidates_scanned||0).toLocaleString();els.underCount.textContent=Number(s.rejected_under_duration||0).toLocaleString();els.notMovieCount.textContent=Number(s.rejected_not_movie||0).toLocaleString();els.wrongLanguageCount.textContent=Number(s.rejected_wrong_language||0).toLocaleString();els.duplicateCount.textContent=Number(s.duplicates_skipped||0).toLocaleString();els.apiCount.textContent=Number(s.api_requests||0).toLocaleString();els.retryCount.textContent=Number(s.network_retries||0).toLocaleString();els.currentQuery.textContent=d.current_query||"Waiting to start";els.discoveryMessage.textContent=d.message||"Your progress is saved automatically.";const dl=downloadsFor(rt);applyStatusPill(els.downloadStatus,dl.status||"IDLE");const dc=state.counts.download||{};const activeJobs=dl.active_jobs||[];const dlWaiting=Number(dc.READY||0)+Number(dc.WAITING_NETWORK||0);els.dashActiveCount.textContent=activeJobs.length.toLocaleString();els.dashQueuedCount.textContent=dlWaiting.toLocaleString();els.dashCompletedCount.textContent=Number(dc.DOWNLOADED||0).toLocaleString();els.dashFailedCount.textContent=Number(dc.FAILED||0).toLocaleString();els.downloadSummary.textContent=activeJobs.length?`Downloading ${activeJobs.length} at once • ${dlWaiting.toLocaleString()} queued • ${Number(dc.DOWNLOADED||0).toLocaleString()} completed${dc.FAILED?` • ${dc.FAILED} could not download`:""}`:(dl.message||"No downloads running yet.");renderActiveJobs(activeJobs);
  // Dashboard "what's happening" progress statement -- one clear sentence
  // instead of several unrelated counters.
  const mode=els.modeSelect?.value||state.mode||"discover";
  const langLabel=state.language?state.language[0].toUpperCase()+state.language.slice(1):"";
  // Discovery reaching its target is NOT the same as the overall job being
  // done -- downloads can still be running. Keep the numbers about discovery
  // only ("found", not "ready") and say so explicitly when mode is "both".
  const discoveryComplete=d.status==="COMPLETED"&&progress>=target&&target>0;
  const downloadsIdle=activeJobs.length===0&&dlWaiting===0;
  const heroTitle=discoveryComplete?"Discovery complete":d.status==="EXHAUSTED"?"Search pool exhausted":mode==="download"?`Downloading your ${langLabel} movies`:mode==="both"?`Finding and downloading your ${langLabel} movies`:`Finding your ${langLabel} movies`;
  els.heroProgressTitle.textContent=heroTitle;
  els.heroProgressCount.innerHTML=`${progress.toLocaleString()} <small>of ${target.toLocaleString()} found</small>`;
  els.heroProgressPct.textContent=`${Math.floor(pct)}%`;
  els.heroProgressFill.style.width=`${pct}%`;
  const overallStatus=(d.status==="ERROR"||dl.status==="ERROR")?"ERROR":(dl.status==="DISK_LOW")?"DISK_LOW":(d.network_wait||dl.network_wait)?"WAITING_NETWORK":(d.status==="RUNNING"||dl.status==="RUNNING")?"RUNNING":(d.status==="PAUSED"||dl.status==="PAUSED")?"PAUSED":discoveryComplete?"COMPLETED":d.status==="EXHAUSTED"?"EXHAUSTED":"IDLE";
  els.heroStatusText.textContent=overallStatus==="WAITING_NETWORK"?"Waiting for internet":overallStatus==="DISK_LOW"?"Paused — low disk space":runtimeStatusLabel(overallStatus);
  els.heroProgressSub.textContent=target===0?"Choose a target and press Start to begin.":discoveryComplete&&mode==="both"?(downloadsIdle?"All movies found and downloaded.":"Downloads are still running below."):discoveryComplete?"All movies found.":progress>=target&&target>0?"Target reached.":overallStatus==="EXHAUSTED"?(d.message||"Search pool exhausted."):overallStatus==="ERROR"&&d.status==="ERROR"?(d.message||"Discovery needs attention."):overallStatus==="IDLE"?"Press Start to begin.":"Movie Manager is working in the background — you can leave this open or come back later.";
  const banner=bannerStatusFor(rt);
  renderStateBanner(els.dashboardStateBanner,banner.status,banner.message);
  renderStateBanner(els.downloadStateBanner,dl.status==="DISK_LOW"?"DISK_LOW":dl.network_wait?"WAITING_NETWORK":null);
  const completedCount=Number(dc.DOWNLOADED||0),failedCount=Number(dc.FAILED||0);
  if(!activeJobs.length&&!dlWaiting&&completedCount){els.downloadSummary.textContent=`${completedCount.toLocaleString()} movies downloaded${failedCount?`; ${failedCount} need attention`:""}.`;}
  if(els.sessionOutcome){
    const show=discoveryComplete&&downloadsIdle&&completedCount>0;
    els.sessionOutcome.classList.toggle("hidden",!show);
    if(show)els.sessionOutcome.innerHTML=`<div><strong>Download session complete</strong><p>${completedCount.toLocaleString()} movies are ready in your download folder${failedCount?`. ${failedCount} need attention`:""}.</p></div><div class="session-outcome-actions"><button class="btn" data-go="downloads">View completed</button><button class="btn primary" data-new-discovery>Find more movies</button></div>`;
  }
  renderLogDiagnostics(d);renderDashboardControls();renderDownloadControls();renderDownloadsPage(state.downloadMovies);}
const PROVIDER_LABELS={internet_archive:"Internet Archive",youtube:"YouTube"};
function providerLabel(m){return PROVIDER_LABELS[m.provider]||"YouTube"}
function fileExtLabel(url){const m=/\.([a-z0-9]{2,4})(?:$|\?)/i.exec(url||"");return m?m[1].toUpperCase():""}
function card(m,selectable){const n=document.createElement("article");n.className="movie-card";n.dataset.cardId=m.id;const st=m.status||"DISCOVERED";const checked=state.selection.has(Number(m.id));if(selectable&&checked)n.classList.add("selected");const checkbox=selectable?`<div class="card-select"><input type="checkbox" data-select="${m.id}" aria-label="Select ${esc(m.title)}" ${checked?"checked":""}></div>`:"";const quickDownload=m.download_status==="READY"?`<button class="mini-btn accent" data-quick-download="${m.id}">Download</button>`:"";const menuItems=(st==="REJECTED"?`<button class="menu-item" data-restore="${m.id}">Restore</button>`:`<button class="menu-item" data-reject="${m.id}">Remove &amp; Replace</button><button class="menu-item" data-reject-wrong-language="${m.id}">Wrong language</button>`)+`<button class="menu-item danger" data-delete="${m.id}">Delete from Library</button>`;n.innerHTML=`${checkbox}<div class="thumb-wrap" data-open="${m.id}">${m.thumbnail_url?`<img loading="lazy" src="${esc(m.thumbnail_url)}" alt="">`:""}<div class="play-badge"><span>▶</span></div><span class="duration-badge">${esc(m.duration_label||"")}</span></div><div class="movie-body"><h4 title="${esc(m.title)}">${esc(m.title)}</h4><div class="movie-meta">${esc(m.channel_title||"Unknown channel")}<br>${m.published_at?new Date(m.published_at).getFullYear():"Unknown year"} • ${esc(movieStatusLabel(st))}</div><div class="movie-actions"><button class="mini-btn" data-open="${m.id}">Details</button><button class="mini-btn" data-youtube="${esc(m.youtube_url)}">${esc(providerLabel(m))}</button>${quickDownload}<div class="card-menu"><button class="mini-btn" data-menu-toggle aria-haspopup="true" aria-expanded="false">More ⋯</button><div class="card-menu-list hidden">${menuItems}</div></div></div></div>`;return n}
function renderGrid(c,ms,selectable,emptyIcon,emptyTitle,emptyBody){c.innerHTML="";if(!ms.length){c.innerHTML=emptyIcon?emptyStateHtml(emptyIcon,emptyTitle,emptyBody):`<div class="empty">No movies here yet.</div>`;return}ms.forEach(m=>c.appendChild(card(m,selectable)))}
function closeCardMenus(){document.querySelectorAll(".card-menu-list:not(.hidden)").forEach(l=>{l.classList.add("hidden");const t=l.previousElementSibling;if(t)t.setAttribute("aria-expanded","false")})}
async function loadMovies(){const status=els.movieStatusFilter?.value||"DOWNLOADED",search=els.movieSearch?.value||"",offset=state.moviesPage*state.moviesPageSize;const [rows,ids,downloadRows]=await Promise.all([api(`/api/movies?language=${encodeURIComponent(state.language)}&status=${encodeURIComponent(status)}&search=${encodeURIComponent(search)}&limit=${state.moviesPageSize}&offset=${offset}`),api(`/api/movies/ids?language=${encodeURIComponent(state.language)}&status=${encodeURIComponent(status)}&search=${encodeURIComponent(search)}`),api(`/api/movies?language=${encodeURIComponent(state.language)}&limit=500`)]);const sort=els.movieSort?.value||"newest";if(sort==="title")rows.sort((a,b)=>String(a.title).localeCompare(String(b.title)));if(sort==="duration")rows.sort((a,b)=>(b.duration_seconds||0)-(a.duration_seconds||0));state.movies=rows;state.downloadMovies=downloadRows;state.moviesTotal=(ids.ids||[]).length;const totalPages=Math.max(1,Math.ceil(state.moviesTotal/state.moviesPageSize));if(state.moviesPage>=totalPages){state.moviesPage=totalPages-1;return loadMovies()}renderGrid(els.allMovies,rows,true,"🎬","No movies here","Try another library section or search term.");renderGrid(els.recentMovies,downloadRows.filter(m=>m.status!=="REJECTED").slice(0,5),false,"🎬","No movies yet","Start discovery to find movies for this language.");renderDownloadsPage(downloadRows);const names={DOWNLOADED:"My library",ACCEPTED:"Ready to download",FAILED:"Needs attention",REJECTED:"Removed archive",ALL:"All movies"};els.moviesTitle.textContent=names[status]||"Movies";els.librarySummary.textContent=`${state.moviesTotal.toLocaleString()} movie${state.moviesTotal===1?"":"s"} • Page ${state.moviesPage+1} of ${totalPages}`;els.moviesPageInfo.textContent=`Page ${state.moviesPage+1} of ${totalPages}`;els.moviesPrevBtn.disabled=state.moviesPage===0;els.moviesNextBtn.disabled=state.moviesPage>=totalPages-1;updateSelectionUI()}
function currentSettingsValues(){return{download_root:els.downloadRoot.value,max_concurrent_downloads:els.maxConcurrentDownloads.value,movies_per_page:els.moviesPerPage.value,min_free_disk_gb:els.minFreeDiskGb.value,download_quality:els.downloadQuality.value,youtube_use_browser_session:els.youtubeUseBrowserSession.checked?"1":"0",youtube_browser:els.youtubeBrowser.value}}
let savedSettingsSnapshot={};
function snapshotSettings(){savedSettingsSnapshot=currentSettingsValues();updateSettingsSaveUI()}
function settingsDirty(){const cur=currentSettingsValues();return Object.keys(cur).some(k=>String(cur[k])!==String(savedSettingsSnapshot[k]))}
function updateSettingsSaveUI(statusOverride){const dirty=settingsDirty();els.saveSettings.disabled=!dirty;if(statusOverride){els.settingsSaveStatus.textContent=statusOverride.text;els.settingsSaveStatus.className=`settings-save-status ${statusOverride.cls||""}`;return}els.settingsSaveStatus.textContent=dirty?"Unsaved changes":"";els.settingsSaveStatus.className="settings-save-status"}
async function bootstrap(){const d=await api("/api/bootstrap");state.language=d.language;state.target=d.target;state.provider=d.provider||"youtube";state.counts=d.counts;state.runtime=d.runtime;state.downloadableOnly=d.settings.count_only_downloadable==="1";renderLanguages(d.languages);els.targetInput.value=state.target;els.downloadRoot.value=d.settings.download_root||"";els.maxConcurrentDownloads.value=d.settings.max_concurrent_downloads||"3";els.moviesPerPage.value=d.settings.movies_per_page||"30";state.moviesPageSize=Number(els.moviesPerPage.value);els.minFreeDiskGb.value=d.settings.min_free_disk_gb||"20";els.downloadQuality.value=d.settings.download_quality||"1080";els.youtubeUseBrowserSession.checked=d.settings.youtube_use_browser_session==="1";els.youtubeBrowser.value=d.settings.youtube_browser||"chrome";els.apiConfigured.textContent=d.api_configured?"Connected":"Not connected";renderStats(d.runtime);snapshotSettings();await loadMovies();await syncLog(true)}
async function refresh(){try{const d=await api("/api/bootstrap");state.language=d.language;state.target=d.target;state.provider=d.provider||"youtube";state.counts=d.counts;state.runtime=d.runtime;state.downloadableOnly=d.settings.count_only_downloadable==="1";els.languageSelect.value=state.language;els.apiConfigured.textContent=d.api_configured?"Connected":"Not connected";renderStats(d.runtime)}catch{}}
async function startAction(){const target=Math.max(1,Number(els.targetInput.value||30));state.target=target;state.mode=els.modeSelect.value;if(state.mode==="discover"||state.mode==="both"){await api("/api/discovery/start",{method:"POST",body:JSON.stringify({language:state.language,target,provider:"youtube"})})}if(state.mode==="download"||state.mode==="both"){await api("/api/downloads/start",{method:"POST",body:JSON.stringify({language:state.language})})}await refresh()}
async function downloadSelected(){const ids=[...state.selection];if(!ids.length)return toast("No movies selected.","error");const r=await api("/api/movies/bulk/download",{method:"POST",body:JSON.stringify({ids,language:state.language})});toast(`${r.queued} queued, ${r.already_downloaded} already downloaded, ${r.could_not_queue} could not be queued.`);clearSelection();await refresh();await loadMovies()}
function toggleSelect(id,checked){id=Number(id);if(checked)state.selection.add(id);else state.selection.delete(id);updateSelectionUI()}
function updateSelectionUI(){const n=state.selection.size;if(els.bulkCount)els.bulkCount.textContent=n===0?"Select movies to manage them":`${n.toLocaleString()} movie${n===1?"":"s"} selected`;if(els.bulkActions)els.bulkActions.classList.toggle("hidden",n===0);if(els.bulkBar)els.bulkBar.classList.toggle("has-selection",n>0);els.selectAllFilteredBtn?.classList.toggle("hidden",n===0);els.clearSelectionBtn?.classList.toggle("hidden",n===0);document.querySelectorAll("#allMovies .movie-card[data-card-id]").forEach(c=>{const id=Number(c.dataset.cardId),sel=state.selection.has(id);c.classList.toggle("selected",sel);const cb=c.querySelector("[data-select]");if(cb)cb.checked=sel});const filter=els.movieStatusFilter?.value||"ALL",showRestoreOnly=filter==="REJECTED";if(els.bulkRestore)els.bulkRestore.classList.toggle("hidden",!showRestoreOnly);if(els.bulkRemoveReplace)els.bulkRemoveReplace.classList.toggle("hidden",showRestoreOnly);if(els.bulkWrongLanguage)els.bulkWrongLanguage.classList.toggle("hidden",showRestoreOnly)}
function selectPage(){state.movies.forEach(m=>state.selection.add(Number(m.id)));updateSelectionUI()}
function clearSelection(){state.selection.clear();updateSelectionUI()}
async function selectAllFiltered(){const status=els.movieStatusFilter?.value||"ALL",search=els.movieSearch?.value||"";const r=await api(`/api/movies/ids?language=${encodeURIComponent(state.language)}&status=${encodeURIComponent(status)}&search=${encodeURIComponent(search)}`);(r.ids||[]).forEach(id=>state.selection.add(Number(id)));updateSelectionUI();toast(`${(r.ids||[]).length.toLocaleString()} movies selected.`)}
async function bulkAction(kind){const ids=[...state.selection];if(!ids.length){toast("No movies selected.","error");return}let confirmMsg="",endpoint="",body={ids,language:state.language};if(kind==="remove"){confirmMsg=`Remove ${ids.length} selected movies and find replacements?`;endpoint="/api/movies/bulk/reject";body.reason="USER_REJECTED"}else if(kind==="wrong-language"){confirmMsg=`Mark ${ids.length} selected movies as wrong language?`;endpoint="/api/movies/bulk/reject";body.reason="WRONG_LANGUAGE"}else if(kind==="restore"){confirmMsg=`Restore ${ids.length} selected movies?`;endpoint="/api/movies/bulk/restore"}if(!confirm(confirmMsg))return;try{const r=await api(endpoint,{method:"POST",body:JSON.stringify(body)});toast(`Updated ${r.updated} of ${r.requested} movies.${r.replacement_triggered?" Replacement discovery started.":""}`);addLog(`Bulk ${kind}: updated ${r.updated}, skipped ${r.skipped}, not found ${r.not_found}.`,kind==="restore"?"log-good":"");clearSelection();await loadMovies();await refresh()}catch(e){toast(e.message,"error")}}
async function showMovie(id){let m=state.movies.find(x=>Number(x.id)===Number(id));if(!m){const rows=await api(`/api/movies?language=${encodeURIComponent(state.language)}&limit=200`);m=rows.find(x=>Number(x.id)===Number(id))}if(!m)return;const label=providerLabel(m);const player=m.download_status==="DOWNLOADED"?`<video class="player" controls preload="metadata" src="/api/movies/${m.id}/local-media"></video>`:m.embeddable?`<iframe class="player" src="https://www.youtube.com/embed/${encodeURIComponent(m.video_id)}" allow="accelerometer; autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>`:`<div class="notice">This source does not embed here. Use "Open on ${esc(label)}".</div>`;const pct=m.total_bytes?Math.min(100,m.bytes_downloaded/m.total_bytes*100):0;const fmt=fileExtLabel(m.download_url);const completedNotice=m.download_status==="DOWNLOADED"?`<div class="notice"><strong>Completed ✓</strong><br>Saved to: ${esc(m.file_path||"")}</div>`:"";
els.modalBody.innerHTML=`${player}<div class="modal-info"><p class="eyebrow">${esc(movieStatusLabel(m.status))} • ${esc(label)}</p><h2 id="modalTitle">${esc(m.title)}</h2><p class="muted">${esc(m.channel_title||"")}</p>${completedNotice}<div class="modal-grid"><div><span>Duration</span><strong>${esc(m.duration_label)}</strong></div><div><span>Published</span><strong>${m.published_at?new Date(m.published_at).toLocaleDateString():"—"}</strong></div><div><span>Download</span><strong>${esc(downloadStatusLabel(m.download_status))}</strong></div><div><span>Format</span><strong>${esc(fmt||"—")}</strong></div><div><span>Downloaded</span><strong>${fmtBytes(m.bytes_downloaded)}</strong></div><div><span>Progress</span><strong>${pct.toFixed(1)}%</strong></div><div><span>Licence / rights</span><strong title="${esc(m.licence||"")}">${esc(m.licence?(m.licence.length>28?m.licence.slice(0,28)+"…":m.licence):"—")}</strong></div></div>${m.source_error?`<div class="notice"><strong>Source issue:</strong> ${esc(m.source_error)}</div>`:""}${m.download_error?`<div class="notice"><strong>Could not download:</strong> ${esc(m.download_error)}</div>`:""}<div class="movie-actions"><button class="btn" data-youtube="${esc(m.youtube_url)}">Open on ${esc(label)}</button>${m.download_status==="FAILED"?`<button class="btn primary" data-retry-download="${m.id}">Retry download</button>`:""}</div><details class="advanced-source"><summary>Advanced / Manual source</summary><div class="source-form"><input id="directSourceInput" placeholder="Authorised direct HTTP(S) movie file URL" value="${esc(m.download_url||"")}" aria-label="Manual direct download URL"><button class="btn primary" id="saveDirectSource" data-id="${m.id}">Set local-download source</button></div><p class="muted" style="margin-top:8px;font-size:12px">Most movies resolve a source automatically -- use this only when automatic resolution has no source.</p></details></div>`;els.movieModal.classList.remove("hidden");els.movieModal.setAttribute("aria-labelledby","modalTitle");els._lastFocused=document.activeElement;els.movieModal.querySelector(".modal-close").focus()}
async function saveSource(id){await api(`/api/movies/${id}/download-source`,{method:"POST",body:JSON.stringify({download_url:$("directSourceInput").value})});toast("Local download source saved.");await loadMovies();await refresh()}
async function deleteFromLibrary(id){if(!confirm("Delete this movie from Movie Manager's library? The downloaded file on your computer will not be deleted."))return;await api(`/api/movies/${id}`,{method:"DELETE"});toast("Movie deleted from the library. Its provider item may be discovered again.");await loadMovies();await refresh()}
async function clearLibrary(){if(!confirm("Clear the Movie Manager catalogue and logical download records? Downloaded movie files and settings will be kept."))return;const r=await api("/api/settings/clear-library",{method:"POST",body:"{}"});toast(`Library cleared (${r.deleted} records). Downloaded files were kept.`);clearSelection();await loadMovies();await refresh()}
async function resetFresh(){if(prompt("Start fresh? This clears catalogue, discovery and download history, rejected-movie memory, and provider scan memory. Downloaded files and settings stay. Type RESET to continue.")!=="RESET")return;const r=await api("/api/settings/start-fresh",{method:"POST",body:"{}"});toast(`Started fresh (${r.deleted} catalogue records cleared). Downloaded files were kept.`);clearSelection();await loadMovies();await refresh()}
function bindResetControls(){els.clearLibraryBtn?.addEventListener("click",()=>clearLibrary().catch(e=>toast(e.message,"error")));els.startFreshBtn?.addEventListener("click",()=>resetFresh().catch(e=>toast(e.message,"error")));document.addEventListener("click",e=>{const b=e.target.closest("[data-delete]");if(b)deleteFromLibrary(b.dataset.delete).catch(x=>toast(x.message,"error"))})}
function closeModal(){els.movieModal.classList.add("hidden");els.modalBody.innerHTML="";if(els._lastFocused&&els._lastFocused.focus)els._lastFocused.focus()}
function network(){const on=navigator.onLine;els.networkDot.className=`dot ${on?"online":"offline"}`;els.networkText.textContent=on?"Browser online":"Browser offline"}
function bind(){document.querySelectorAll(".nav-item").forEach(b=>b.addEventListener("click",()=>{document.querySelectorAll(".nav-item").forEach(x=>{x.classList.remove("active");x.removeAttribute("aria-current")});b.classList.add("active");b.setAttribute("aria-current","page");document.querySelectorAll(".view").forEach(x=>x.classList.remove("active"));$(`view-${b.dataset.view}`).classList.add("active");$("pageTitle").textContent=b.textContent.trim()}));document.addEventListener("keydown",e=>{if(e.key==="Escape"){if(!els.movieModal.classList.contains("hidden"))closeModal();closeCardMenus()}});document.querySelectorAll("[data-go]").forEach(b=>b.addEventListener("click",()=>document.querySelector(`.nav-item[data-view="${b.dataset.go}"]`).click()));els.languageSelect.addEventListener("change",async()=>{state.language=els.languageSelect.value;clearSelection();await api("/api/settings",{method:"POST",body:JSON.stringify({active_language:state.language})});await bootstrap()});els.selectPageBtn.addEventListener("click",selectPage);els.selectAllFilteredBtn.addEventListener("click",()=>selectAllFiltered().catch(e=>toast(e.message,"error")));els.clearSelectionBtn.addEventListener("click",clearSelection);els.bulkRemoveReplace.addEventListener("click",()=>bulkAction("remove"));els.bulkWrongLanguage.addEventListener("click",()=>bulkAction("wrong-language"));els.bulkRestore.addEventListener("click",()=>bulkAction("restore"));document.body.addEventListener("change",e=>{const cb=e.target.closest("[data-select]");if(cb)toggleSelect(cb.dataset.select,cb.checked)});els.primaryActionBtn.addEventListener("click",()=>dashboardPrimaryAction());els.secondaryStopBtn.addEventListener("click",()=>dashboardStopAction());els.targetInput.addEventListener("input",()=>renderDashboardControls());els.modeSelect.addEventListener("change",()=>{state.mode=els.modeSelect.value;renderDashboardControls()});els.downloadPrimaryBtn.addEventListener("click",()=>downloadPrimaryAction());els.downloadStopBtn.addEventListener("click",()=>downloadStopAction());els.dashDownloadPrimaryBtn?.addEventListener("click",()=>downloadPrimaryAction());els.dashDownloadStopBtn?.addEventListener("click",()=>downloadStopAction());els.refreshMovies.addEventListener("click",()=>loadMovies().catch(e=>toast(e.message,"error")));els.movieStatusFilter.addEventListener("change",()=>{clearSelection();loadMovies().catch(()=>{})});let timer;els.movieSearch.addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>{clearSelection();loadMovies().catch(()=>{})},250)});els.clearActivity.addEventListener("click",()=>{els.activityLog.innerHTML="";api("/api/log/clear",{method:"POST",body:"{}"}).catch(()=>{})});els.chooseFolder.addEventListener("click",async()=>{try{const r=await api("/api/settings/select-folder",{method:"POST",body:"{}"});if(r.path){els.downloadRoot.value=r.path;updateSettingsSaveUI()}}catch(e){toast(e.message,"error")}});["maxConcurrentDownloads","downloadQuality","minFreeDiskGb","youtubeUseBrowserSession","youtubeBrowser"].forEach(id=>els[id].addEventListener("change",()=>updateSettingsSaveUI()));els.downloadRoot.addEventListener("input",()=>updateSettingsSaveUI());els.saveSettings.addEventListener("click",async()=>{try{const r=await api("/api/settings",{method:"POST",body:JSON.stringify(currentSettingsValues())});els.downloadRoot.value=r.settings.download_root||"";els.maxConcurrentDownloads.value=r.settings.max_concurrent_downloads;els.minFreeDiskGb.value=r.settings.min_free_disk_gb;els.downloadQuality.value=r.settings.download_quality||els.downloadQuality.value;els.youtubeUseBrowserSession.checked=r.settings.youtube_use_browser_session==="1";els.youtubeBrowser.value=r.settings.youtube_browser||els.youtubeBrowser.value;snapshotSettings();updateSettingsSaveUI({text:"Settings saved ✓",cls:"ok"});toast("Settings saved.");await refresh()}catch(e){updateSettingsSaveUI({text:"Could not save settings",cls:"error"});toast(e.message,"error")}});document.body.addEventListener("click",async e=>{const o=e.target.closest("[data-open]"),yt=e.target.closest("[data-youtube]"),r=e.target.closest("[data-reject]"),wl=e.target.closest("[data-reject-wrong-language]"),rs=e.target.closest("[data-restore]"),cl=e.target.closest("[data-close-modal]"),sv=e.target.closest("#saveDirectSource"),retryDl=e.target.closest("[data-retry-download]"),mt=e.target.closest("[data-menu-toggle]"),qd=e.target.closest("[data-quick-download]");if(mt){e.stopPropagation();const list=mt.nextElementSibling;const willOpen=list.classList.contains("hidden");closeCardMenus();if(willOpen){list.classList.remove("hidden");mt.setAttribute("aria-expanded","true")}return}if(!e.target.closest(".card-menu"))closeCardMenus();if(qd){try{const r=await api("/api/movies/bulk/download",{method:"POST",body:JSON.stringify({ids:[Number(qd.dataset.quickDownload)],language:state.language})});toast(r.queued?"Movie queued for download.":"Could not queue this movie.");await refresh();await loadMovies()}catch(x){toast(x.message,"error")}return}if(o)showMovie(o.dataset.open).catch(x=>toast(x.message,"error"));if(yt)window.open(yt.dataset.youtube,"_blank","noopener");if(cl)closeModal();if(sv)saveSource(sv.dataset.id).catch(x=>toast(x.message,"error"));if(retryDl){api(`/api/movies/${retryDl.dataset.retryDownload}/retry-download`,{method:"POST"}).then(()=>{toast("Download queued for retry.");return api("/api/downloads/start",{method:"POST",body:JSON.stringify({language:state.language})})}).catch(x=>toast(x.message,"error"))}if(r&&confirm("Remove this movie and prevent the same video from being accepted again?")){try{await api(`/api/movies/${r.dataset.reject}/reject`,{method:"POST",body:JSON.stringify({reason:"USER_REJECTED"})});toast("Movie removed. Replacement can be discovered automatically.");await loadMovies();await refresh()}catch(x){toast(x.message,"error")}}if(wl&&confirm("Reject this movie as wrong language and find a replacement?")){try{await api(`/api/movies/${wl.dataset.rejectWrongLanguage}/reject`,{method:"POST",body:JSON.stringify({reason:"WRONG_LANGUAGE"})});toast("Movie marked as wrong language. Replacement can be discovered automatically.");await loadMovies();await refresh()}catch(x){toast(x.message,"error")}}if(rs){try{await api(`/api/movies/${rs.dataset.restore}/restore`,{method:"POST"});toast("Movie restored.");await loadMovies();await refresh()}catch(x){toast(x.message,"error")}}});window.addEventListener("online",network);window.addEventListener("offline",network)}
function findActiveJob(movieId){const jobs=state.runtime?.downloads?.active_jobs;return jobs&&jobs.find(j=>Number(j.movie_id)===Number(movieId))}
function applyDownloadProgress(p){
  const job=findActiveJob(p.movie_id);
  if(job){
    job.bytes_downloaded=p.bytes_downloaded;job.total_bytes=p.total_bytes;
    job.speed_bps=p.speed_bps;job.eta_seconds=p.eta_seconds;
    // A progress tick only ever fires while bytes are actually flowing, so
    // the job is DOWNLOADING now even if its last known stage was QUEUED.
    job.stage="DOWNLOADING";
  }
  renderStats(state.runtime);
}
function applyDownloadStage(p){
  const job=findActiveJob(p.movie_id);
  if(job)job.stage=p.stage;
  renderStats(state.runtime);
}
function events(){const es=new EventSource("/api/events");es.addEventListener("movie_processed",async e=>{const d=JSON.parse(e.data).payload,m=d.movie,res=d.result;scheduleRefresh(res==="ACCEPTED"||res==="TARGET_REACHED")});es.addEventListener("source_resolved",async e=>{const p=JSON.parse(e.data).payload;if(p.source_status==="SOURCE_INVALID")addLog(`Source invalid for movie #${p.movie_id}: ${p.error||""}`,"log-bad");await refresh();await loadMovies()});es.addEventListener("sources_bulk_resolved",async e=>{const p=JSON.parse(e.data).payload;addLog(`Bulk source resolution: ${p.ready} ready, ${p.missing} missing, ${p.invalid} invalid.`,"log-good");await refresh();await loadMovies()});es.addEventListener("discovery_status",async e=>{const p=JSON.parse(e.data).payload;state.runtime=state.runtime||{};state.runtime.discovery=p;renderStats(state.runtime);if(["COMPLETED","EXHAUSTED","ERROR","STOPPED"].includes(p.status)){scheduleRefresh(true)}});es.addEventListener("download_status",e=>{const p=JSON.parse(e.data).payload;state.runtime=state.runtime||{};state.runtime.downloads=p;renderStats(state.runtime)});es.addEventListener("download_progress",e=>{const p=JSON.parse(e.data).payload;applyDownloadProgress(p)});
es.addEventListener("download_stage",e=>{const p=JSON.parse(e.data).payload;applyDownloadStage(p)});
es.addEventListener("log",e=>pushLogEntry(JSON.parse(e.data).payload));
es.addEventListener("open",()=>syncLog(false));
es.addEventListener("download_complete",async e=>{const p=JSON.parse(e.data).payload;toast("Movie download completed.");await refresh();await loadMovies()});es.addEventListener("network_wait",()=>{});es.addEventListener("download_retry",async e=>{await refresh();await loadMovies()});es.addEventListener("error",async e=>{if(!e.data)return;const p=JSON.parse(e.data).payload;toast(p.message||"An error occurred.","error");if(p.scope==="download"){await refresh();await loadMovies()}})}
function bindExperienceImprovements(){
  document.querySelectorAll("[data-download-tab]").forEach(button=>button.addEventListener("click",()=>setDownloadTab(button.dataset.downloadTab)));
  els.downloadsPrevBtn?.addEventListener("click",()=>{state.downloadPages[state.downloadTab]=Math.max(0,state.downloadPages[state.downloadTab]-1);renderDownloadsPage(state.downloadMovies)});
  els.downloadsNextBtn?.addEventListener("click",()=>{state.downloadPages[state.downloadTab]++;renderDownloadsPage(state.downloadMovies)});
  els.retryFailedBtn?.addEventListener("click",async()=>{try{const r=await api("/api/downloads/retry-failed",{method:"POST",body:JSON.stringify({language:state.language})});toast(r.retried?"One failed movie was queued for another safe attempt.":"There are no failed movies to retry.");await refresh();await loadMovies()}catch(e){toast(e.message,"error")}});
  document.body.addEventListener("click",async e=>{
    const fresh=e.target.closest("[data-new-discovery]"),replace=e.target.closest("[data-replace-movie]"),go=e.target.closest("[data-go]");
    if(go){document.querySelector(`.nav-item[data-view="${go.dataset.go}"]`)?.click();return;}
    if(fresh){state.mode="discover";els.modeSelect.value="discover";els.targetInput.value=Number(state.counts.accepted||0)+30;els.discoverySetup?.classList.remove("hidden");renderDashboardControls();els.targetInput.focus();window.scrollTo({top:0,behavior:"smooth"});}
    if(replace){const id=replace.dataset.replaceMovie;if(!confirm("Remove this unavailable movie and look for a replacement?"))return;try{await api(`/api/movies/${id}/reject`,{method:"POST",body:JSON.stringify({reason:"USER_REJECTED"})});toast("Movie removed. A replacement can now be found.");closeModal();await loadMovies();await refresh()}catch(err){toast(err.message,"error")}}
  });
  document.querySelectorAll("[data-library-status]").forEach(button=>button.addEventListener("click",()=>{state.moviesPage=0;els.movieStatusFilter.value=button.dataset.libraryStatus;document.querySelectorAll("[data-library-status]").forEach(tab=>{const active=tab===button;tab.classList.toggle("active",active);tab.setAttribute("aria-selected",String(active))});clearSelection();loadMovies().catch(e=>toast(e.message,"error"));}));
  els.moviesPrevBtn?.addEventListener("click",()=>{if(state.moviesPage>0){state.moviesPage--;clearSelection();loadMovies().catch(e=>toast(e.message,"error"))}});
  els.moviesNextBtn?.addEventListener("click",()=>{state.moviesPage++;clearSelection();loadMovies().catch(e=>toast(e.message,"error"))});
  els.movieSort?.addEventListener("change",()=>{state.moviesPage=0;loadMovies().catch(e=>toast(e.message,"error"))});
  els.moviesPerPage?.addEventListener("change",()=>{state.moviesPageSize=Number(els.moviesPerPage.value);state.moviesPage=0;updateSettingsSaveUI();loadMovies().catch(e=>toast(e.message,"error"))});
  els.maxConcurrentDownloads?.addEventListener("change",async()=>{
    const running=["RUNNING","PAUSED","WAITING","DISK_LOW"].includes(state.runtime?.downloads?.status);
    if(!running)return;
    try{const r=await api("/api/settings",{method:"POST",body:JSON.stringify({max_concurrent_downloads:els.maxConcurrentDownloads.value})});els.maxConcurrentDownloads.value=r.settings.max_concurrent_downloads;savedSettingsSnapshot.max_concurrent_downloads=els.maxConcurrentDownloads.value;updateSettingsSaveUI({text:"Download speed updated immediately",cls:"ok"});toast(`Now downloading up to ${els.maxConcurrentDownloads.value} movies at once.`);await refresh()}catch(e){toast(e.message,"error")}
  });
  document.body.addEventListener("click",e=>{const page=e.target.closest("[data-movies-page]"),downloadPage=e.target.closest("[data-download-page]");if(downloadPage){state.downloadPages[state.downloadTab]=Number(downloadPage.dataset.downloadPage);renderDownloadsPage(state.downloadMovies);return}if(page){state.moviesPage=Number(page.dataset.moviesPage);clearSelection();loadMovies().catch(err=>toast(err.message,"error"));}});
}
function renderPageNumbers(){
  if(!els.moviesPageNumbers)return;
  const total=Math.max(1,Math.ceil(state.moviesTotal/state.moviesPageSize)),current=state.moviesPage;
  const pages=total<=7?[...Array(total).keys()]:[0,...new Set([Math.max(1,current-1),current,Math.min(total-2,current+1)]),total-1];
  els.moviesPageNumbers.innerHTML=pages.map((page,index)=>`${index&&page>pages[index-1]+1?'<span>…</span>':""}<button class="page-number ${page===current?"active":""}" data-movies-page="${page}" ${page===current?'aria-current="page"':""}>${page+1}</button>`).join("");
}
document.addEventListener("DOMContentLoaded",()=>setInterval(renderPageNumbers,500));
document.addEventListener("DOMContentLoaded",async()=>{cache();els.bulkDownloadSelected?.addEventListener("click",()=>downloadSelected().catch(e=>toast(e.message,"error")));bind();bindResetControls();bindExperienceImprovements();network();events();try{await bootstrap();const active=state.runtime?.downloads?.active_jobs?.length||0,failed=state.movies.some(m=>m.download_status==="FAILED"),waiting=state.movies.some(m=>["READY","QUEUED","WAITING_NETWORK"].includes(m.download_status)),completed=state.movies.some(m=>m.download_status==="DOWNLOADED");setDownloadTab(active?"downloading":failed?"failed":waiting?"queued":completed?"completed":"downloading");setInterval(refresh,5000)}catch(e){toast(e.message,"error")}});
