const state={language:"yoruba",target:30,provider:"youtube",movies:[],runtime:null,counts:{},mode:"discover",selection:new Set(),downloadableOnly:false};
const $=id=>document.getElementById(id);const els={};
function cache(){["languageSelect","targetInput","modeSelect","providerSelect","primaryActionBtn","secondaryStopBtn","statTarget","statAccepted","statDownloadable","statDownloaded","statRemaining","statSourceMissing","statSourceInvalid","discoveryStatus","downloadStatus","discoveryBar","discoveryProgressText","discoveryPercent","scannedCount","underCount","notMovieCount","wrongLanguageCount","duplicateCount","apiCount","retryCount","currentQuery","discoveryMessage","recentMovies","allMovies","activityLog","downloadSummary","movieSearch","movieStatusFilter","resolveSourcesBtn","refreshMovies","downloadPrimaryBtn","downloadStopBtn","minimumDuration","maintainTarget","countOnlyDownloadable","maxConcurrentDownloads","minFreeDiskGb","downloadRoot","chooseFolder","saveSettings","apiConfigured","movieModal","modalBody","toastWrap","networkDot","networkText","clearActivity","moviesTitle","selectPageBtn","selectAllFilteredBtn","clearSelectionBtn","bulkCount","bulkActions","bulkRemoveReplace","bulkWrongLanguage","bulkRestore","importFormat","importContent","importSourcesBtn","importResult","clearLibraryBtn","startFreshBtn","supplyScanStatus","supplyScanMax","supplyScanStartBtn","supplyScanStopBtn","supplyScanQuery","supplyScanMessage","supplyScanResultsWrap","supplyScanResults","ssSearched","ssChecked","ssQualifying","ssReady","ssUnder","ssWrongLang","ssNotMovie","ssCompilation","ssDuplicate","ssAmbiguous","ssNoFile","ssTempErr","ssPermErr","dashboardStateBanner","downloadStateBanner","downloadsPageStateBanner","heroProgressTitle","heroProgressSub","heroProgressCount","heroProgressPct","heroProgressFill","heroStatusText","activeJobsGrid","dgDownloading","dgDownloadingCount","dgQueued","dgQueuedCount","dgCompleted","dgCompletedCount","dgFailed","dgFailedCount","bulkBar"].forEach(id=>els[id]=$(id));}
// New YouTube-only controls are intentionally kept outside the legacy cache list.
els.downloadQuality=$("downloadQuality");els.bulkDownloadSelected=$("bulkDownloadSelected");
async function api(url,options={}){const opts={headers:{"Content-Type":"application/json"},...options};const r=await fetch(url,opts);let b={};try{b=await r.json()}catch{}if(!r.ok||b.ok===false)throw new Error(b.error||`Request failed (${r.status})`);return b}
function esc(v=""){return String(v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]))}
function toast(m,t=""){const n=document.createElement("div");n.className=`toast ${t}`;n.textContent=m;els.toastWrap.appendChild(n);setTimeout(()=>n.remove(),4200)}
function addLog(m,k=""){const n=document.createElement("div");n.className=`log-line ${k}`;const t=new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"});n.innerHTML=`<span class="log-time">${t}</span><span>${esc(m)}</span>`;els.activityLog.prepend(n);while(els.activityLog.children.length>80)els.activityLog.lastChild.remove()}
function fmtBytes(n){if(n==null)return"—";let v=Number(n),u=["B","KB","MB","GB","TB"],i=0;while(v>=1024&&i<u.length-1){v/=1024;i++}return`${v.toFixed(i?1:0)} ${u[i]}`}
function fmtTime(s){if(s==null||!isFinite(s))return"—";s=Math.max(0,Math.round(s));if(s<60)return`${s}s`;const m=Math.floor(s/60),x=s%60;if(m<60)return`${m}m ${x}s`;return`${Math.floor(m/60)}h ${m%60}m`}
const PHASE_PRIORITY=["error","disk-low","waiting-network","running","paused","stopping","continue","completed","waiting-source","idle"];
const DASHBOARD_PHASE_LABELS={
  idle:{label:"Start",cls:"primary",disabled:false},
  running:{label:"Pause",cls:"primary",disabled:false},
  "waiting-network":{label:"Waiting for internet…",cls:"",disabled:true},
  "disk-low":{label:"Low disk space",cls:"",disabled:true},
  paused:{label:"Resume",cls:"primary",disabled:false},
  stopping:{label:"Stopping…",cls:"",disabled:true},
  completed:{label:"✓ Completed",cls:"",disabled:true},
  continue:{label:"Continue",cls:"primary",disabled:false},
  error:{label:"Retry",cls:"primary",disabled:false},
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
function deriveDiscoveryPhase(){
  const d=state.runtime?.discovery||{},status=d.status||"IDLE";
  const target=Math.max(1,Number(els.targetInput?.value||state.target||0));
  const progress=state.downloadableOnly?Number(state.counts.downloadable||0):Number(state.counts.accepted||0);
  if(status==="ERROR")return"error";
  if(status==="RUNNING")return d.network_wait?"waiting-network":"running";
  if(status==="PAUSED")return"paused";
  if(status==="STOPPING")return"stopping";
  if(status==="COMPLETED")return target>progress?"continue":"completed";
  return"idle"
}
function deriveDownloadPhase(){
  const dl=state.runtime?.downloads||{},status=dl.status||"IDLE";
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
  const showStop=["running","waiting-network","disk-low","paused","stopping","waiting-source"].includes(phase);
  els.secondaryStopBtn.classList.toggle("hidden",!showStop);
  els.secondaryStopBtn.disabled=phase==="stopping";
  const locked=["running","paused","waiting-network","disk-low","stopping"].includes(phase);
  els.languageSelect.disabled=locked;
  els.modeSelect.disabled=locked;
  els.providerSelect.disabled=locked;
  els.targetInput.disabled=phase==="running"||phase==="waiting-network"||phase==="stopping";
}
function renderDownloadControls(){
  const phase=deriveDownloadPhase(),info=DOWNLOAD_PHASE_LABELS[phase]||DOWNLOAD_PHASE_LABELS.idle;
  applyButtonPhase(els.downloadPrimaryBtn,info);
  const showStop=["running","waiting-network","disk-low","paused","stopping","waiting-source"].includes(phase);
  els.downloadStopBtn.classList.toggle("hidden",!showStop);
  els.downloadStopBtn.disabled=phase==="stopping";
}
async function dashboardPrimaryAction(){
  const mode=els.modeSelect.value,phase=combineDashboardPhase(),dPhase=deriveDiscoveryPhase(),dlPhase=deriveDownloadPhase();
  const target=Math.max(1,Number(els.targetInput.value||state.target||30));
  try{
    if(phase==="idle"){await startAction();return}
    if(phase==="continue"){
      state.target=target;
      await api("/api/discovery/start",{method:"POST",body:JSON.stringify({language:state.language,target,provider:els.providerSelect.value})});
      addLog(`Continuing discovery toward new target: ${target}.`,"log-good");
      await refresh();return
    }
    if(phase==="error"){
      const tasks=[];
      if((mode==="discover"||mode==="both")&&dPhase==="error")tasks.push(api("/api/discovery/start",{method:"POST",body:JSON.stringify({language:state.language,target,provider:els.providerSelect.value})}));
      if((mode==="download"||mode==="both")&&dlPhase==="error")tasks.push(api("/api/downloads/start",{method:"POST",body:JSON.stringify({language:state.language})}));
      await Promise.all(tasks);
      addLog("Retrying after error.","log-good");
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
        tasks.push(api("/api/discovery/start",{method:"POST",body:JSON.stringify({language:state.language,target,provider:els.providerSelect.value})}));
      }
      if(mode==="download"||mode==="both")tasks.push(api("/api/downloads/resume",{method:"POST"}));
      await Promise.all(tasks);
      addLog("Resumed.","log-good");
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
    if(phase==="idle"||phase==="error"){await api("/api/downloads/start",{method:"POST",body:JSON.stringify({language:state.language})});addLog(phase==="error"?"Retrying downloads after error.":"Download worker started.","log-good")}
    else if(phase==="running"){await api("/api/downloads/pause",{method:"POST"})}
    else if(phase==="paused"){await api("/api/downloads/resume",{method:"POST"})}
    await refresh()
  }catch(e){toast(e.message,"error")}
}
async function downloadStopAction(){try{await api("/api/downloads/stop",{method:"POST"});await refresh()}catch(e){toast(e.message,"error")}}
function renderLanguages(l){els.languageSelect.innerHTML="";Object.entries(l).forEach(([k,p])=>{const o=document.createElement("option");o.value=k;o.textContent=p.enabled?p.label:`${p.label} — later`;o.disabled=!p.enabled;els.languageSelect.appendChild(o)});els.languageSelect.value=state.language}
const RUNTIME_STATUS_LABELS={IDLE:"Idle",RUNNING:"Running",PAUSED:"Paused",STOPPING:"Stopping…",STOPPED:"Stopped",COMPLETED:"Completed",ERROR:"Needs attention",WAITING:"Waiting for downloadable movies",DISK_LOW:"Paused — low disk space"};
function runtimeStatusLabel(status){return RUNTIME_STATUS_LABELS[status]||status||"Idle"}
const JOB_STAGE_LABELS={QUEUED:"Queued",PREPARING:"Preparing",DOWNLOADING:"Downloading",MERGING:"Combining video and audio",VERIFYING:"Checking movie",FINALISING:"Finishing movie",RETRY_WAIT:"Trying again shortly"};
function jobStageLabel(stage){return JOB_STAGE_LABELS[stage]||"Downloading"}
const MOVIE_STATUS_LABELS={DISCOVERED:"Found",ACCEPTED:"Accepted",QUEUED:"Queued",DOWNLOADING:"Downloading",DOWNLOADED:"Downloaded",REJECTED:"Removed"};
function movieStatusLabel(status){return MOVIE_STATUS_LABELS[status]||status||""}
const DOWNLOAD_STATUS_LABELS={NOT_READY:"Not ready yet",READY:"Ready to download",QUEUED:"Queued",DOWNLOADING:"Downloading",WAITING_NETWORK:"Waiting for internet",DOWNLOADED:"Completed",FAILED:"Could not download"};
function downloadStatusLabel(status){return DOWNLOAD_STATUS_LABELS[status]||"Not ready yet"}
function statusPillClass(status){if(status==="ERROR")return"warn";if(["RUNNING","COMPLETED"].includes(status))return"ok";if(["DISK_LOW"].includes(status))return"warn";return"neutral"}
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
  const d=rt?.discovery||{},dl=rt?.downloads||{};
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
function downloadRowHtml(m,kind){
  const thumb=m.thumbnail_url?`<img loading="lazy" src="${esc(m.thumbnail_url)}" alt="">`:"";
  let sub="";
  if(kind==="downloading"){
    const pct=m.total_bytes?Math.min(100,(m.bytes_downloaded||0)/m.total_bytes*100):null;
    sub=`${jobStageLabel(m._stage||"DOWNLOADING")}${pct!=null?` • ${pct.toFixed(0)}%`:""}${m.download_speed_bps?` • ${fmtBytes(m.download_speed_bps)}/s`:""}`;
  }else if(kind==="queued"){
    sub="Waiting for a free download slot";
  }else if(kind==="completed"){
    sub=m.file_path?`Saved to ${m.file_path}`:"Completed";
  }else if(kind==="failed"){
    sub=m.download_error?`Could not download — ${m.download_error}`:"Could not download";
  }
  return `<div class="download-row ${kind}" data-open="${m.id}">
    <div class="download-row-thumb">${thumb}</div>
    <div class="download-row-body">
      <div class="download-row-title">${esc(m.title)}</div>
      <div class="download-row-sub">${esc(sub)}</div>
    </div>
    <div class="download-row-stat">${kind==="downloading"&&m.total_bytes?`<strong>${fmtBytes(m.bytes_downloaded)} / ${fmtBytes(m.total_bytes)}</strong>`:""}</div>
  </div>`;
}
function renderDownloadsPage(rows){
  const activeIds=new Set((state.runtime?.downloads?.active_jobs||[]).map(j=>Number(j.movie_id)));
  const stageByMovie={};
  (state.runtime?.downloads?.active_jobs||[]).forEach(j=>{stageByMovie[j.movie_id]=j.stage});
  const downloading=[],queued=[],completed=[],failed=[];
  rows.forEach(m=>{
    const id=Number(m.id);
    if(activeIds.has(id)){m._stage=stageByMovie[id];downloading.push(m)}
    else if(m.download_status==="DOWNLOADED")completed.push(m);
    else if(m.download_status==="FAILED")failed.push(m);
    else if(["READY","WAITING_NETWORK","QUEUED"].includes(m.download_status)||m.downloadable)queued.push(m);
  });
  els.dgDownloadingCount.textContent=downloading.length;
  els.dgQueuedCount.textContent=queued.length;
  els.dgCompletedCount.textContent=completed.length;
  els.dgFailedCount.textContent=failed.length;
  els.dgDownloading.innerHTML=downloading.length?downloading.map(m=>downloadRowHtml(m,"downloading")).join(""):emptyStateHtml("⬇️","Nothing downloading right now","Press Start downloads to begin.");
  els.dgQueued.innerHTML=queued.length?queued.map(m=>downloadRowHtml(m,"queued")).join(""):emptyStateHtml("🕓","No downloads queued","Movies ready to download will appear here.");
  els.dgCompleted.innerHTML=completed.length?completed.map(m=>downloadRowHtml(m,"completed")).join(""):emptyStateHtml("✅","No completed movies yet","Finished movies will appear here automatically.");
  els.dgFailed.innerHTML=failed.length?failed.map(m=>downloadRowHtml(m,"failed")).join(""):emptyStateHtml("✔️","No failed downloads","Movie Manager will show anything it couldn't download here.");
}
function renderStats(rt){const target=Number(state.target||0),accepted=Number(state.counts.accepted||0),downloadable=Number(state.counts.downloadable||0),downloaded=Number(state.counts.downloaded||0),sourceMissing=Number(state.counts.source_missing||0),sourceInvalid=Number(state.counts.source_invalid||0),progress=state.downloadableOnly?downloadable:accepted,rem=Math.max(target-progress,0);els.statTarget.textContent=target.toLocaleString();els.statAccepted.textContent=accepted.toLocaleString();els.statDownloadable.textContent=downloadable.toLocaleString();els.statDownloaded.textContent=downloaded.toLocaleString();els.statRemaining.textContent=rem.toLocaleString();els.statSourceMissing.textContent=sourceMissing.toLocaleString();els.statSourceInvalid.textContent=sourceInvalid.toLocaleString();const pct=target?Math.min(100,progress/target*100):0;els.discoveryBar.style.width=`${pct}%`;els.discoveryProgressText.textContent=`${progress.toLocaleString()} / ${target.toLocaleString()}`;els.discoveryPercent.textContent=`${pct.toFixed(1)}%`;const d=rt?.discovery||{},s=d.stats||{};applyStatusPill(els.discoveryStatus,d.status||"IDLE");els.scannedCount.textContent=Number(s.candidates_scanned||0).toLocaleString();els.underCount.textContent=Number(s.rejected_under_duration||0).toLocaleString();els.notMovieCount.textContent=Number(s.rejected_not_movie||0).toLocaleString();els.wrongLanguageCount.textContent=Number(s.rejected_wrong_language||0).toLocaleString();els.duplicateCount.textContent=Number(s.duplicates_skipped||0).toLocaleString();els.apiCount.textContent=Number(s.api_requests||0).toLocaleString();els.retryCount.textContent=Number(s.network_retries||0).toLocaleString();els.currentQuery.textContent=d.current_query||"Waiting to start";els.discoveryMessage.textContent=d.message||"Your progress is saved automatically.";const dl=rt?.downloads||{};applyStatusPill(els.downloadStatus,dl.status||"IDLE");const dc=state.counts.download||{};const activeJobs=dl.active_jobs||[];const dlWaiting=Number(dc.READY||0)+Number(dc.WAITING_NETWORK||0);els.downloadSummary.textContent=activeJobs.length?`Downloading ${activeJobs.length} at once • ${dlWaiting.toLocaleString()} queued • ${Number(dc.DOWNLOADED||0).toLocaleString()} completed${dc.FAILED?` • ${dc.FAILED} could not download`:""}`:(dl.message||"No downloads running yet.");renderActiveJobs(activeJobs);
  // Dashboard "what's happening" progress statement -- one clear sentence
  // instead of several unrelated counters.
  const mode=els.modeSelect?.value||state.mode||"discover";
  const langLabel=state.language?state.language[0].toUpperCase()+state.language.slice(1):"";
  const heroTitle=mode==="download"?`Downloading your ${langLabel} movies`:mode==="both"?`Finding and downloading your ${langLabel} movies`:`Finding your ${langLabel} movies`;
  els.heroProgressTitle.textContent=heroTitle;
  els.heroProgressCount.innerHTML=`${progress.toLocaleString()} <small>of ${target.toLocaleString()} ready</small>`;
  els.heroProgressPct.textContent=`${pct.toFixed(0)}%`;
  els.heroProgressFill.style.width=`${pct}%`;
  const overallStatus=(d.status==="ERROR"||dl.status==="ERROR")?"ERROR":(dl.status==="DISK_LOW")?"DISK_LOW":(d.network_wait||dl.network_wait)?"WAITING_NETWORK":(d.status==="RUNNING"||dl.status==="RUNNING")?"RUNNING":(d.status==="PAUSED"||dl.status==="PAUSED")?"PAUSED":(d.status==="COMPLETED"&&progress>=target&&target>0)?"COMPLETED":"IDLE";
  els.heroStatusText.textContent=overallStatus==="WAITING_NETWORK"?"Waiting for internet":overallStatus==="DISK_LOW"?"Paused — low disk space":runtimeStatusLabel(overallStatus);
  els.heroProgressSub.textContent=target===0?"Choose a target and press Start to begin.":progress>=target&&target>0?"Target reached.":overallStatus==="IDLE"?"Press Start to begin.":"Movie Manager is working in the background — you can leave this open or come back later.";
  const banner=bannerStatusFor(rt);
  renderStateBanner(els.dashboardStateBanner,banner.status,banner.message);
  renderStateBanner(els.downloadStateBanner,dl.status==="DISK_LOW"?"DISK_LOW":dl.network_wait?"WAITING_NETWORK":null);
  renderDashboardControls();renderDownloadControls();renderSupplyScan(rt?.supply_scan);}
function renderSupplyScan(s){if(!s)return;els.supplyScanStatus.textContent=s.status||"IDLE";const running=["RUNNING","PAUSED","STOPPING"].includes(s.status);els.supplyScanStartBtn.disabled=running;els.supplyScanMax.disabled=running;els.supplyScanStopBtn.classList.toggle("hidden",!running);const c=s.counters||{};els.ssSearched.textContent=Number(c.candidates_searched||0).toLocaleString();els.ssChecked.textContent=Number(c.metadata_checked||0).toLocaleString();els.ssQualifying.textContent=Number(c.qualifying_60min||0).toLocaleString();els.ssReady.textContent=Number(c.source_ready||0).toLocaleString();els.ssUnder.textContent=Number(c.under_60_minutes||0).toLocaleString();els.ssWrongLang.textContent=Number(c.wrong_language||0).toLocaleString();els.ssNotMovie.textContent=Number(c.not_movie||0).toLocaleString();els.ssCompilation.textContent=Number(c.compilation_trailer_etc||0).toLocaleString();els.ssDuplicate.textContent=Number(c.duplicate||0).toLocaleString();els.ssAmbiguous.textContent=Number(c.ambiguous_rights||0).toLocaleString();els.ssNoFile.textContent=Number(c.no_usable_video_file||0).toLocaleString();els.ssTempErr.textContent=Number(c.temporary_archive_errors||0).toLocaleString();els.ssPermErr.textContent=Number(c.permanent_provider_errors||0).toLocaleString();els.supplyScanQuery.textContent=s.status==="IDLE"?"Waiting to start":`${s.provider||""} • ${s.language||""}`;els.supplyScanMessage.textContent=s.message||"Not started yet.";const qualifying=s.qualifying||[];els.supplyScanResultsWrap.style.display=qualifying.length?"block":"none";if(qualifying.length){els.supplyScanResults.innerHTML=qualifying.map(q=>`<div class="log-line log-good"><span class="log-time">${Math.round((q.duration_seconds||0)/60)}m</span><span>${esc(q.title||q.identifier)} — ${esc(q.licence||"")}</span></div>`).join("")}}
async function startSupplyScan(){const provider=els.providerSelect.value==="internet_archive"?els.providerSelect.value:"internet_archive";const max_candidates=Number(els.supplyScanMax.value||250);await api("/api/supply-scan/start",{method:"POST",body:JSON.stringify({language:state.language,provider,max_candidates})});addLog(`Supply scan started: ${provider}, up to ${max_candidates} candidates.`,"log-good");await refresh()}
async function stopSupplyScan(){await api("/api/supply-scan/stop",{method:"POST"});await refresh()}
const SOURCE_BADGE_INFO={SOURCE_READY:["ready","Ready"],SOURCE_MISSING:["missing","Missing"],SOURCE_INVALID:["invalid","Invalid"],SOURCE_PENDING:["pending","Pending"]};
function sourceBadgeHtml(status){const[cls,label]=SOURCE_BADGE_INFO[status]||SOURCE_BADGE_INFO.SOURCE_PENDING;return`<span class="source-badge ${cls}">Source: ${label}</span>`}
const PROVIDER_LABELS={internet_archive:"Internet Archive",youtube:"YouTube"};
function providerLabel(m){return PROVIDER_LABELS[m.provider]||"YouTube"}
function fileExtLabel(url){const m=/\.([a-z0-9]{2,4})(?:$|\?)/i.exec(url||"");return m?m[1].toUpperCase():""}
function card(m,selectable){const n=document.createElement("article");n.className="movie-card";n.dataset.cardId=m.id;const st=m.status||"DISCOVERED";const checked=state.selection.has(Number(m.id));if(selectable&&checked)n.classList.add("selected");const checkbox=selectable?`<div class="card-select"><input type="checkbox" data-select="${m.id}" aria-label="Select ${esc(m.title)}" ${checked?"checked":""}></div>`:"";n.innerHTML=`${checkbox}<div class="thumb-wrap" data-open="${m.id}">${m.thumbnail_url?`<img loading="lazy" src="${esc(m.thumbnail_url)}" alt="">`:""}<div class="play-badge"><span>▶</span></div><span class="duration-badge">${esc(m.duration_label||"")}</span></div><div class="movie-body"><h4 title="${esc(m.title)}">${esc(m.title)}</h4><div class="movie-meta">${esc(m.channel_title||"Unknown channel")}<br>${m.published_at?new Date(m.published_at).getFullYear():"Unknown year"} • ${esc(movieStatusLabel(st))} • ${esc(providerLabel(m))}${sourceBadgeHtml(m.source_status)}</div><div class="movie-actions"><button class="mini-btn" data-open="${m.id}">Details</button><button class="mini-btn" data-youtube="${esc(m.youtube_url)}">${esc(providerLabel(m))}</button>${st==="REJECTED"?`<button class="mini-btn red" data-restore="${m.id}">Restore</button>`:`<button class="mini-btn red" data-reject="${m.id}">Remove & Replace</button><button class="mini-btn red" data-reject-wrong-language="${m.id}">Wrong language</button>`}</div></div>`;return n}
function renderGrid(c,ms,selectable,emptyIcon,emptyTitle,emptyBody){c.innerHTML="";if(!ms.length){c.innerHTML=emptyIcon?emptyStateHtml(emptyIcon,emptyTitle,emptyBody):`<div class="empty">No movies here yet.</div>`;return}ms.forEach(m=>{const n=card(m,selectable);const a=n.querySelector(".movie-actions");if(a){const b=document.createElement("button");b.className="mini-btn red";b.dataset.delete=m.id;b.textContent="Delete from Library";a.appendChild(b)}c.appendChild(n)})}
async function loadMovies(){const status=els.movieStatusFilter?.value||"ALL",search=els.movieSearch?.value||"";const rows=await api(`/api/movies?language=${encodeURIComponent(state.language)}&status=${encodeURIComponent(status)}&search=${encodeURIComponent(search)}&limit=120`);state.movies=rows;renderGrid(els.allMovies,rows,true,"🎬","No movies yet","Start discovery to find movies for this language.");renderGrid(els.recentMovies,rows.filter(m=>m.status!=="REJECTED").slice(0,8),false,"🎬","No movies yet","Start discovery to find movies for this language.");renderDownloadsPage(rows);els.moviesTitle.textContent=`${state.language[0].toUpperCase()+state.language.slice(1)} Movies`;updateSelectionUI()}
async function bootstrap(){const d=await api("/api/bootstrap");state.language=d.language;state.target=d.target;state.provider=d.provider||"youtube";state.counts=d.counts;state.runtime=d.runtime;state.downloadableOnly=d.settings.count_only_downloadable==="1";renderLanguages(d.languages);els.targetInput.value=state.target;els.providerSelect.value=state.provider;els.downloadRoot.value=d.settings.download_root||"";els.maintainTarget.checked=d.settings.maintain_target==="1";els.countOnlyDownloadable.checked=state.downloadableOnly;els.maxConcurrentDownloads.value=d.settings.max_concurrent_downloads||"3";els.minFreeDiskGb.value=d.settings.min_free_disk_gb||"20";els.apiConfigured.textContent=d.api_configured?"CONNECTED":"ADD KEY";renderStats(d.runtime);await loadMovies()}
async function refresh(){try{const d=await api("/api/bootstrap");state.language=d.language;state.target=d.target;state.provider=d.provider||"youtube";state.counts=d.counts;state.runtime=d.runtime;state.downloadableOnly=d.settings.count_only_downloadable==="1";els.languageSelect.value=state.language;if(document.activeElement!==els.providerSelect)els.providerSelect.value=state.provider;els.apiConfigured.textContent=d.api_configured?"CONNECTED":"ADD KEY";renderStats(d.runtime)}catch{}}
async function startAction(){const target=Math.max(1,Number(els.targetInput.value||30));state.target=target;state.mode=els.modeSelect.value;if(state.mode==="discover"||state.mode==="both"){await api("/api/discovery/start",{method:"POST",body:JSON.stringify({language:state.language,target,provider:els.providerSelect.value})});addLog(`Discovery started. Target: ${target}.`,"log-good")}if(state.mode==="download"||state.mode==="both"){await api("/api/downloads/start",{method:"POST",body:JSON.stringify({language:state.language})});addLog("Download worker started.","log-good")}await refresh()}
async function downloadSelected(){const ids=[...state.selection];if(!ids.length)return toast("No movies selected.","error");const r=await api("/api/movies/bulk/download",{method:"POST",body:JSON.stringify({ids,language:state.language})});toast(`${r.queued} queued, ${r.already_downloaded} already downloaded, ${r.could_not_queue} could not be queued.`);clearSelection();await refresh();await loadMovies()}
function toggleSelect(id,checked){id=Number(id);if(checked)state.selection.add(id);else state.selection.delete(id);updateSelectionUI()}
function updateSelectionUI(){const n=state.selection.size;if(els.bulkCount)els.bulkCount.textContent=n===0?"Tap movies to select them":`${n.toLocaleString()} movie${n===1?"":"s"} selected`;if(els.bulkActions)els.bulkActions.classList.toggle("hidden",n===0);if(els.bulkBar)els.bulkBar.classList.toggle("has-selection",n>0);document.querySelectorAll("#allMovies .movie-card[data-card-id]").forEach(c=>{const id=Number(c.dataset.cardId),sel=state.selection.has(id);c.classList.toggle("selected",sel);const cb=c.querySelector("[data-select]");if(cb)cb.checked=sel});const filter=els.movieStatusFilter?.value||"ALL",showRestoreOnly=filter==="REJECTED";if(els.bulkRestore)els.bulkRestore.classList.toggle("hidden",!showRestoreOnly);if(els.bulkRemoveReplace)els.bulkRemoveReplace.classList.toggle("hidden",showRestoreOnly);if(els.bulkWrongLanguage)els.bulkWrongLanguage.classList.toggle("hidden",showRestoreOnly)}
function selectPage(){state.movies.forEach(m=>state.selection.add(Number(m.id)));updateSelectionUI()}
function clearSelection(){state.selection.clear();updateSelectionUI()}
async function selectAllFiltered(){const status=els.movieStatusFilter?.value||"ALL",search=els.movieSearch?.value||"";const r=await api(`/api/movies/ids?language=${encodeURIComponent(state.language)}&status=${encodeURIComponent(status)}&search=${encodeURIComponent(search)}`);(r.ids||[]).forEach(id=>state.selection.add(Number(id)));updateSelectionUI();toast(`${(r.ids||[]).length.toLocaleString()} movies selected.`)}
async function bulkAction(kind){const ids=[...state.selection];if(!ids.length){toast("No movies selected.","error");return}let confirmMsg="",endpoint="",body={ids,language:state.language};if(kind==="remove"){confirmMsg=`Remove ${ids.length} selected movies and find replacements?`;endpoint="/api/movies/bulk/reject";body.reason="USER_REJECTED"}else if(kind==="wrong-language"){confirmMsg=`Mark ${ids.length} selected movies as wrong language?`;endpoint="/api/movies/bulk/reject";body.reason="WRONG_LANGUAGE"}else if(kind==="restore"){confirmMsg=`Restore ${ids.length} selected movies?`;endpoint="/api/movies/bulk/restore"}if(!confirm(confirmMsg))return;try{const r=await api(endpoint,{method:"POST",body:JSON.stringify(body)});toast(`Updated ${r.updated} of ${r.requested} movies.${r.replacement_triggered?" Replacement discovery started.":""}`);addLog(`Bulk ${kind}: updated ${r.updated}, skipped ${r.skipped}, not found ${r.not_found}.`,kind==="restore"?"log-good":"");clearSelection();await loadMovies();await refresh()}catch(e){toast(e.message,"error")}}
async function resolveSources(){const ids=[...state.selection];const body={language:state.language};if(ids.length)body.ids=ids;const r=await api("/api/movies/bulk/resolve-sources",{method:"POST",body:JSON.stringify(body)});toast(`Checked ${r.checked}: ${r.ready} ready, ${r.missing} missing, ${r.invalid} invalid${r.errors?`, ${r.errors} errors`:""}.`);addLog(`Resolved sources for ${ids.length?"selected":"all accepted"} movies: ${r.ready} ready, ${r.missing} missing, ${r.invalid} invalid.`,"log-good");await loadMovies();await refresh()}
async function importSources(){const format=els.importFormat.value,content=els.importContent.value;if(!content.trim()){toast("Paste CSV or JSON content first.","error");return}const r=await api("/api/download-sources/import",{method:"POST",body:JSON.stringify({format,content})});els.importResult.textContent=`Imported ${r.imported} • Updated ${r.updated} • Invalid ${r.invalid} • Unknown video IDs ${r.unknown_video_ids} • Duplicates ${r.duplicates}`;toast("Source mappings imported.");await refresh()}
async function showMovie(id){let m=state.movies.find(x=>Number(x.id)===Number(id));if(!m){const rows=await api(`/api/movies?language=${encodeURIComponent(state.language)}&limit=200`);m=rows.find(x=>Number(x.id)===Number(id))}if(!m)return;const label=providerLabel(m);const player=m.download_status==="DOWNLOADED"?`<video class="player" controls preload="metadata" src="/api/movies/${m.id}/local-media"></video>`:m.embeddable?`<iframe class="player" src="https://www.youtube.com/embed/${encodeURIComponent(m.video_id)}" allow="accelerometer; autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>`:`<div class="notice">This source does not embed here. Use "Open on ${esc(label)}".</div>`;const pct=m.total_bytes?Math.min(100,m.bytes_downloaded/m.total_bytes*100):0;const fmt=fileExtLabel(m.download_url);const completedNotice=m.download_status==="DOWNLOADED"?`<div class="notice"><strong>Completed ✓</strong><br>Saved to: ${esc(m.file_path||"")}</div>`:"";
els.modalBody.innerHTML=`${player}<div class="modal-info"><p class="eyebrow">${esc(movieStatusLabel(m.status))} • ${esc(label)}</p><h2 id="modalTitle">${esc(m.title)}</h2><p class="muted">${esc(m.channel_title||"")}</p>${completedNotice}<div class="modal-grid"><div><span>Duration</span><strong>${esc(m.duration_label)}</strong></div><div><span>Published</span><strong>${m.published_at?new Date(m.published_at).toLocaleDateString():"—"}</strong></div><div><span>Download</span><strong>${esc(downloadStatusLabel(m.download_status))}</strong></div><div><span>Source</span><strong>${sourceBadgeHtml(m.source_status)}</strong></div><div><span>Format</span><strong>${esc(fmt||"—")}</strong></div><div><span>Downloaded</span><strong>${fmtBytes(m.bytes_downloaded)}</strong></div><div><span>Progress</span><strong>${pct.toFixed(1)}%</strong></div><div><span>Licence / rights</span><strong title="${esc(m.licence||"")}">${esc(m.licence?(m.licence.length>28?m.licence.slice(0,28)+"…":m.licence):"—")}</strong></div></div>${m.source_error?`<div class="notice"><strong>Source issue:</strong> ${esc(m.source_error)}</div>`:""}${m.download_error?`<div class="notice"><strong>Could not download:</strong> ${esc(m.download_error)}</div>`:""}<div class="movie-actions"><button class="btn" data-youtube="${esc(m.youtube_url)}">Open on ${esc(label)}</button>${m.download_status==="FAILED"?`<button class="btn primary" data-retry-download="${m.id}">Retry download</button>`:""}</div><details class="advanced-source"><summary>Advanced / Manual source</summary><div class="source-form"><input id="directSourceInput" placeholder="Authorised direct HTTP(S) movie file URL" value="${esc(m.download_url||"")}" aria-label="Manual direct download URL"><button class="btn primary" id="saveDirectSource" data-id="${m.id}">Set local-download source</button></div><p class="muted" style="margin-top:8px;font-size:12px">Most movies resolve a source automatically -- use this only when automatic resolution has no source.</p></details></div>`;els.movieModal.classList.remove("hidden");els.movieModal.setAttribute("aria-labelledby","modalTitle");els._lastFocused=document.activeElement;els.movieModal.querySelector(".modal-close").focus()}
async function saveSource(id){await api(`/api/movies/${id}/download-source`,{method:"POST",body:JSON.stringify({download_url:$("directSourceInput").value})});toast("Local download source saved.");await loadMovies();await refresh()}
async function deleteFromLibrary(id){if(!confirm("Delete this movie from Movie Manager's library? The downloaded file on your computer will not be deleted."))return;await api(`/api/movies/${id}`,{method:"DELETE"});toast("Movie deleted from the library. Its provider item may be discovered again.");await loadMovies();await refresh()}
async function clearLibrary(){if(!confirm("Clear the Movie Manager catalogue and logical download records? Downloaded movie files and settings will be kept."))return;const r=await api("/api/settings/clear-library",{method:"POST",body:"{}"});toast(`Library cleared (${r.deleted} records). Downloaded files were kept.`);clearSelection();await loadMovies();await refresh()}
async function resetFresh(){if(prompt("Start fresh? This clears catalogue, discovery and download history, rejected-movie memory, and provider scan memory. Downloaded files and settings stay. Type RESET to continue.")!=="RESET")return;const r=await api("/api/settings/start-fresh",{method:"POST",body:"{}"});toast(`Started fresh (${r.deleted} catalogue records cleared). Downloaded files were kept.`);clearSelection();await loadMovies();await refresh()}
function bindResetControls(){els.clearLibraryBtn?.addEventListener("click",()=>clearLibrary().catch(e=>toast(e.message,"error")));els.startFreshBtn?.addEventListener("click",()=>resetFresh().catch(e=>toast(e.message,"error")));document.addEventListener("click",e=>{const b=e.target.closest("[data-delete]");if(b)deleteFromLibrary(b.dataset.delete).catch(x=>toast(x.message,"error"))})}
function closeModal(){els.movieModal.classList.add("hidden");els.modalBody.innerHTML="";if(els._lastFocused&&els._lastFocused.focus)els._lastFocused.focus()}
function network(){const on=navigator.onLine;els.networkDot.className=`dot ${on?"online":"offline"}`;els.networkText.textContent=on?"Browser online":"Browser offline"}
function bind(){document.querySelectorAll(".nav-item").forEach(b=>b.addEventListener("click",()=>{document.querySelectorAll(".nav-item").forEach(x=>{x.classList.remove("active");x.removeAttribute("aria-current")});b.classList.add("active");b.setAttribute("aria-current","page");document.querySelectorAll(".view").forEach(x=>x.classList.remove("active"));$(`view-${b.dataset.view}`).classList.add("active");$("pageTitle").textContent=b.textContent.trim()}));document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!els.movieModal.classList.contains("hidden"))closeModal()});document.querySelectorAll("[data-go]").forEach(b=>b.addEventListener("click",()=>document.querySelector(`.nav-item[data-view="${b.dataset.go}"]`).click()));els.languageSelect.addEventListener("change",async()=>{state.language=els.languageSelect.value;clearSelection();await api("/api/settings",{method:"POST",body:JSON.stringify({active_language:state.language})});await bootstrap()});els.selectPageBtn.addEventListener("click",selectPage);els.selectAllFilteredBtn.addEventListener("click",()=>selectAllFiltered().catch(e=>toast(e.message,"error")));els.clearSelectionBtn.addEventListener("click",clearSelection);els.bulkRemoveReplace.addEventListener("click",()=>bulkAction("remove"));els.bulkWrongLanguage.addEventListener("click",()=>bulkAction("wrong-language"));els.bulkRestore.addEventListener("click",()=>bulkAction("restore"));document.body.addEventListener("change",e=>{const cb=e.target.closest("[data-select]");if(cb)toggleSelect(cb.dataset.select,cb.checked)});els.primaryActionBtn.addEventListener("click",()=>dashboardPrimaryAction());els.secondaryStopBtn.addEventListener("click",()=>dashboardStopAction());els.targetInput.addEventListener("input",()=>renderDashboardControls());els.modeSelect.addEventListener("change",()=>{state.mode=els.modeSelect.value;renderDashboardControls()});els.downloadPrimaryBtn.addEventListener("click",()=>downloadPrimaryAction());els.downloadStopBtn.addEventListener("click",()=>downloadStopAction());els.refreshMovies.addEventListener("click",()=>loadMovies().catch(e=>toast(e.message,"error")));els.movieStatusFilter.addEventListener("change",()=>{clearSelection();loadMovies().catch(()=>{})});let timer;els.movieSearch.addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>{clearSelection();loadMovies().catch(()=>{})},250)});els.clearActivity.addEventListener("click",()=>els.activityLog.innerHTML="");els.chooseFolder.addEventListener("click",async()=>{try{const r=await api("/api/settings/select-folder",{method:"POST",body:"{}"});if(r.path)els.downloadRoot.value=r.path}catch(e){toast(e.message,"error")}});els.saveSettings.addEventListener("click",async()=>{try{const r=await api("/api/settings",{method:"POST",body:JSON.stringify({download_root:els.downloadRoot.value,maintain_target:els.maintainTarget.checked?"1":"0",count_only_downloadable:els.countOnlyDownloadable.checked?"1":"0",max_concurrent_downloads:els.maxConcurrentDownloads.value,min_free_disk_gb:els.minFreeDiskGb.value})});state.downloadableOnly=els.countOnlyDownloadable.checked;els.maxConcurrentDownloads.value=r.settings.max_concurrent_downloads;els.minFreeDiskGb.value=r.settings.min_free_disk_gb;toast("Settings saved.");await refresh()}catch(e){toast(e.message,"error")}});els.resolveSourcesBtn.addEventListener("click",()=>resolveSources().catch(e=>toast(e.message,"error")));els.importSourcesBtn.addEventListener("click",()=>importSources().catch(e=>toast(e.message,"error")));els.supplyScanStartBtn.addEventListener("click",()=>startSupplyScan().catch(e=>toast(e.message,"error")));els.supplyScanStopBtn.addEventListener("click",()=>stopSupplyScan().catch(e=>toast(e.message,"error")));document.body.addEventListener("click",async e=>{const o=e.target.closest("[data-open]"),yt=e.target.closest("[data-youtube]"),r=e.target.closest("[data-reject]"),wl=e.target.closest("[data-reject-wrong-language]"),rs=e.target.closest("[data-restore]"),cl=e.target.closest("[data-close-modal]"),sv=e.target.closest("#saveDirectSource"),retryDl=e.target.closest("[data-retry-download]");if(o)showMovie(o.dataset.open).catch(x=>toast(x.message,"error"));if(yt)window.open(yt.dataset.youtube,"_blank","noopener");if(cl)closeModal();if(sv)saveSource(sv.dataset.id).catch(x=>toast(x.message,"error"));if(retryDl){api(`/api/movies/${retryDl.dataset.retryDownload}/retry-download`,{method:"POST"}).then(()=>{toast("Download queued for retry.");return api("/api/downloads/start",{method:"POST",body:JSON.stringify({language:state.language})})}).catch(x=>toast(x.message,"error"))}if(r&&confirm("Remove this movie and prevent the same video from being accepted again?")){try{await api(`/api/movies/${r.dataset.reject}/reject`,{method:"POST",body:JSON.stringify({reason:"USER_REJECTED"})});toast("Movie removed. Replacement can be discovered automatically.");await loadMovies();await refresh()}catch(x){toast(x.message,"error")}}if(wl&&confirm("Reject this movie as wrong language and find a replacement?")){try{await api(`/api/movies/${wl.dataset.rejectWrongLanguage}/reject`,{method:"POST",body:JSON.stringify({reason:"WRONG_LANGUAGE"})});toast("Movie marked as wrong language. Replacement can be discovered automatically.");await loadMovies();await refresh()}catch(x){toast(x.message,"error")}}if(rs){try{await api(`/api/movies/${rs.dataset.restore}/restore`,{method:"POST"});toast("Movie restored.");await loadMovies();await refresh()}catch(x){toast(x.message,"error")}}});window.addEventListener("online",network);window.addEventListener("offline",network)}
function movieTitleById(id){const m=state.movies.find(x=>Number(x.id)===Number(id));if(m)return m.title;const j=(state.runtime?.downloads?.active_jobs||[]).find(x=>Number(x.movie_id)===Number(id));return j?.title||`Movie #${id}`}
const progressLogState={};
function events(){const es=new EventSource("/api/events");es.addEventListener("movie_processed",async e=>{const d=JSON.parse(e.data).payload,m=d.movie,res=d.result;const msg=res==="ACCEPTED"?`Accepted: ${m.title}`:res==="TARGET_REACHED"?`Target already reached, kept as pending: ${m.title}`:res==="UNDER_DURATION"?`Skipped under 60m: ${m.title}`:res==="WRONG_LANGUAGE"?`Skipped wrong language: ${m.title}`:res==="DUPLICATE"?`Duplicate skipped: ${m.title}`:`Rejected: ${m.title}`;addLog(msg,res==="ACCEPTED"?"log-good":"");await refresh();if(res==="ACCEPTED"||res==="TARGET_REACHED")await loadMovies()});es.addEventListener("source_resolved",async e=>{const p=JSON.parse(e.data).payload;if(p.source_status==="SOURCE_INVALID")addLog(`Source invalid for movie #${p.movie_id}: ${p.error||""}`,"log-bad");await refresh();await loadMovies()});es.addEventListener("sources_bulk_resolved",async e=>{const p=JSON.parse(e.data).payload;addLog(`Bulk source resolution: ${p.ready} ready, ${p.missing} missing, ${p.invalid} invalid.`,"log-good");await refresh();await loadMovies()});es.addEventListener("discovery_status",async e=>{const p=JSON.parse(e.data).payload;state.runtime=state.runtime||{};state.runtime.discovery=p;renderStats(state.runtime);if(["COMPLETED","ERROR","STOPPED"].includes(p.status)){addLog(`Discovery ${p.status.toLowerCase()}: ${p.message}`,p.status==="ERROR"?"log-bad":"");await refresh();await loadMovies()}});es.addEventListener("download_status",e=>{const p=JSON.parse(e.data).payload;state.runtime=state.runtime||{};state.runtime.downloads=p;renderStats(state.runtime);addLog(p.message||`Download status: ${p.status}`)});es.addEventListener("download_progress",e=>{const p=JSON.parse(e.data).payload;const jobs=state.runtime?.downloads?.active_jobs;const job=jobs&&jobs.find(j=>Number(j.movie_id)===Number(p.movie_id));if(job){job.bytes_downloaded=p.bytes_downloaded;job.total_bytes=p.total_bytes;job.speed_bps=p.speed_bps;job.eta_seconds=p.eta_seconds;renderActiveJobs(jobs)}
  const pct=p.total_bytes?Math.min(100,(p.bytes_downloaded||0)/p.total_bytes*100):null;
  const last=progressLogState[p.movie_id]||{time:0,pct:-100};
  const now=Date.now();
  if(now-last.time>=2500||(pct!=null&&Math.abs(pct-last.pct)>=5)){
    progressLogState[p.movie_id]={time:now,pct:pct==null?last.pct:pct};
    const title=movieTitleById(p.movie_id);
    const pctLabel=pct!=null?`${pct.toFixed(0)}%`:"";
    const speedLabel=p.speed_bps?`${fmtBytes(p.speed_bps)}/s`:"";
    addLog(`↓ ${title}${pctLabel?" — "+pctLabel:""}${speedLabel?" — "+speedLabel:""}`);
  }
});
es.addEventListener("download_stage",e=>{const p=JSON.parse(e.data).payload;if(p.stage==="MERGING")addLog("Combining video and audio...");else if(p.stage==="VERIFYING")addLog("Checking movie...")});
es.addEventListener("download_complete",async e=>{const p=JSON.parse(e.data).payload;delete progressLogState[p.movie_id];addLog(`✓ ${movieTitleById(p.movie_id)} completed`,"log-good");toast("Movie download completed.");await refresh();await loadMovies()});es.addEventListener("network_wait",e=>{const p=JSON.parse(e.data).payload;addLog(`Network/source interruption. Auto retry in ${p.delay}s. ${p.error||""}`,"log-bad")});es.addEventListener("supply_scan_status",e=>{const p=JSON.parse(e.data).payload;state.runtime=state.runtime||{};state.runtime.supply_scan=p;renderSupplyScan(p);if(["COMPLETED","ERROR","STOPPED"].includes(p.status)){addLog(`Supply scan ${p.status.toLowerCase()}: ${p.message}`,p.status==="ERROR"?"log-bad":"log-good")}});es.addEventListener("error",e=>{const p=JSON.parse(e.data).payload;addLog(`${p.scope||"App"} error: ${p.message}`,"log-bad");toast(p.message||"An error occurred.","error")})}
document.addEventListener("DOMContentLoaded",async()=>{cache();els.bulkDownloadSelected?.addEventListener("click",()=>downloadSelected().catch(e=>toast(e.message,"error")));els.downloadQuality?.addEventListener("change",()=>api("/api/settings",{method:"POST",body:JSON.stringify({download_quality:els.downloadQuality.value})}).catch(e=>toast(e.message,"error")));bind();bindResetControls();network();events();try{await bootstrap();if(els.downloadQuality)els.downloadQuality.value=(await api("/api/bootstrap")).settings.download_quality||"1080";setInterval(refresh,5000)}catch(e){toast(e.message,"error")}});
