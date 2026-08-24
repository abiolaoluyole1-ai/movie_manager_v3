// Plain-Node behavioral test for the state-aware dashboard/download controls
// in static/app.js. No test framework: loads the real shipped file into a
// sandboxed vm context (via a small DOM stub) and exercises the exact
// exported phase-derivation and control-rendering functions used by the app.
//
// Run with: node tests/test_dashboard_controls.js

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function makeEl() {
  const listeners = {};
  let _text = "", _html = "";
  const el = {
    value: "", disabled: false, checked: false,
    dataset: {}, className: "", style: {}, _kids: [],
    // Real DOM string-coerces on assignment (el.textContent = 5 reads back "5");
    // a plain object property would keep the raw number and break equality checks.
    get textContent() { return _text; }, set textContent(v) { _text = String(v); },
    get innerHTML() { return _html; }, set innerHTML(v) { _html = String(v); },
    get children() { return el._kids; },
    get lastChild() { return el._kids[el._kids.length - 1]; },
    classList: {
      _set: new Set(),
      toggle(cls, force) {
        if (force === undefined) {
          if (this._set.has(cls)) this._set.delete(cls); else this._set.add(cls);
        } else if (force) this._set.add(cls); else this._set.delete(cls);
        return this._set.has(cls);
      },
      add(cls) { this._set.add(cls); },
      remove(cls) { this._set.delete(cls); },
      contains(cls) { return this._set.has(cls); },
    },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    _listeners: listeners,
    appendChild(child) { el._kids.push(child); return child; },
    prepend(child) { el._kids.unshift(child); },
    querySelector() { return null; }, closest() { return null; },
  };
  return el;
}

function makeCreatedEl(tag) {
  return { tagName: tag, className: "", innerHTML: "", textContent: "", remove() {} };
}

const elementRegistry = {};
function getElementById(id) {
  if (!elementRegistry[id]) elementRegistry[id] = makeEl();
  return elementRegistry[id];
}

const documentStub = {
  addEventListener() {}, getElementById, querySelectorAll() { return []; },
  createElement: makeCreatedEl,
  body: makeEl(),
};

const sandbox = {
  document: documentStub,
  window: {},
  navigator: { onLine: true },
  console,
  fetch: async () => ({ ok: true, json: async () => ([]) }),
  EventSource: class { addEventListener() {} },
  setInterval() {}, setTimeout() {}, clearTimeout() {},
};
vm.createContext(sandbox);

const src = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
const trailer = `
this.__TEST__ = {
  state, els, deriveDiscoveryPhase, deriveDownloadPhase, combineDashboardPhase,
  renderDashboardControls, renderDownloadControls, clearSelection, toggleSelect,
  renderStats, renderDownloadsPage, downloadRowHtml,
  applyDownloadProgress, applyDownloadStage, logDownloadProgress, progressLogState,
};
`;
vm.runInContext(src + trailer, sandbox, { filename: "app.js" });

const T = sandbox.__TEST__;

let failures = 0;
function assertEqual(actual, expected, msg) {
  if (actual !== expected) {
    failures++;
    console.error(`FAIL: ${msg} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  } else {
    console.log(`ok: ${msg}`);
  }
}

T.els.targetInput = makeEl(); T.els.targetInput.value = "30";
T.els.modeSelect = makeEl(); T.els.modeSelect.value = "discover";
T.els.primaryActionBtn = makeEl();
T.els.secondaryStopBtn = makeEl();
T.els.languageSelect = makeEl();
T.els.downloadPrimaryBtn = makeEl();
T.els.downloadStopBtn = makeEl();
T.els.movieStatusFilter = makeEl();

// 1. IDLE shows Start only
T.state.runtime = { discovery: { status: "IDLE" }, downloads: { status: "IDLE" } };
T.state.counts = { accepted: 0 };
T.renderDashboardControls();
assertEqual(T.els.primaryActionBtn.textContent, "Start", "IDLE primary label is Start");
assertEqual(T.els.primaryActionBtn.disabled, false, "IDLE primary is enabled");
assertEqual(T.els.secondaryStopBtn.classList.contains("hidden"), true, "IDLE hides Stop");

// 2. RUNNING shows Pause + Stop, locks language/mode/target
T.state.runtime.discovery = { status: "RUNNING", network_wait: false };
T.renderDashboardControls();
assertEqual(T.els.primaryActionBtn.textContent, "Pause", "RUNNING primary label is Pause");
assertEqual(T.els.secondaryStopBtn.classList.contains("hidden"), false, "RUNNING shows Stop");
assertEqual(T.els.languageSelect.disabled, true, "RUNNING locks language selector");
assertEqual(T.els.modeSelect.disabled, true, "RUNNING locks mode selector");
assertEqual(T.els.targetInput.disabled, true, "RUNNING locks target input");

// 3. PAUSED shows Resume + Stop; target stays editable, language/mode stay locked
T.state.runtime.discovery = { status: "PAUSED", network_wait: false };
T.renderDashboardControls();
assertEqual(T.els.primaryActionBtn.textContent, "Resume", "PAUSED primary label is Resume");
assertEqual(T.els.secondaryStopBtn.classList.contains("hidden"), false, "PAUSED shows Stop");
assertEqual(T.els.targetInput.disabled, false, "PAUSED leaves target editable");
assertEqual(T.els.languageSelect.disabled, true, "PAUSED still locks language selector");
assertEqual(T.els.modeSelect.disabled, true, "PAUSED still locks mode selector");

// 4. COMPLETED (target satisfied) shows Completed with no active controls
T.state.runtime.discovery = { status: "COMPLETED", network_wait: false };
T.state.counts = { accepted: 30 };
T.els.targetInput.value = "30";
T.renderDashboardControls();
assertEqual(T.els.primaryActionBtn.textContent, "✓ Completed", "COMPLETED shows checkmark label");
assertEqual(T.els.primaryActionBtn.disabled, true, "COMPLETED primary is disabled");
assertEqual(T.els.secondaryStopBtn.classList.contains("hidden"), true, "COMPLETED hides Stop");

// 5. Raising target above accepted total after completion produces Continue
T.els.targetInput.value = "100";
T.renderDashboardControls();
assertEqual(T.els.primaryActionBtn.textContent, "Continue", "Raised target shows Continue");
assertEqual(T.els.primaryActionBtn.disabled, false, "Continue primary is enabled");
assertEqual(T.els.secondaryStopBtn.classList.contains("hidden"), true, "Continue hides Stop");

// 7. ERROR shows Retry only
T.state.runtime.discovery = { status: "ERROR" };
T.renderDashboardControls();
assertEqual(T.els.primaryActionBtn.textContent, "Retry", "ERROR shows Retry");
assertEqual(T.els.secondaryStopBtn.classList.contains("hidden"), true, "ERROR hides Stop");

// 8. Network-wait state does not require manual Resume; Stop still available
T.state.runtime.discovery = { status: "RUNNING", network_wait: true };
T.renderDashboardControls();
assertEqual(T.els.primaryActionBtn.textContent, "Waiting for internet…", "waiting-network label");
assertEqual(T.els.primaryActionBtn.disabled, true, "waiting-network primary is disabled");
assertEqual(T.els.secondaryStopBtn.classList.contains("hidden"), false, "waiting-network still shows Stop");

// back to running (recovered) automatically returns to Pause/Stop
T.state.runtime.discovery = { status: "RUNNING", network_wait: false };
T.renderDashboardControls();
assertEqual(T.els.primaryActionBtn.textContent, "Pause", "recovers to Pause after network wait clears");

// 11. Download controls use the same state-aware behavior, independent of Dashboard mode
T.state.runtime.downloads = { status: "RUNNING", network_wait: false };
T.renderDownloadControls();
assertEqual(T.els.downloadPrimaryBtn.textContent, "Pause", "download RUNNING label is Pause");
assertEqual(T.els.downloadStopBtn.classList.contains("hidden"), false, "download RUNNING shows Stop");

T.state.runtime.downloads = { status: "WAITING" };
T.renderDownloadControls();
assertEqual(T.els.downloadPrimaryBtn.textContent, "No downloads queued", "download WAITING label");
assertEqual(T.els.downloadPrimaryBtn.disabled, true, "download WAITING primary disabled");
assertEqual(T.els.downloadStopBtn.classList.contains("hidden"), false, "download WAITING still shows Stop");

T.state.runtime.downloads = { status: "ERROR" };
T.renderDownloadControls();
assertEqual(T.els.downloadPrimaryBtn.textContent, "Retry", "download ERROR shows Retry");
assertEqual(T.els.downloadStopBtn.classList.contains("hidden"), true, "download ERROR hides Stop");

T.state.runtime.downloads = { status: "IDLE" };
T.renderDownloadControls();
assertEqual(T.els.downloadPrimaryBtn.textContent, "Start downloads", "download IDLE label");

// 14/15/16: bulk-selection helpers used by the search/status-filter change handlers
T.state.selection = new Set([1, 2, 3]);
T.clearSelection();
assertEqual(T.state.selection.size, 0, "clearSelection empties the selection");

T.state.selection = new Set();
T.toggleSelect(4, true);
assertEqual(T.state.selection.has(4), true, "toggleSelect adds an id");
T.toggleSelect(4, false);
assertEqual(T.state.selection.has(4), false, "toggleSelect removes an id");

// ---------------------------------------------------------------------------
// Live download controls / live Downloads page correction pass
// ---------------------------------------------------------------------------

// Wire up every element renderStats()/renderDownloadsPage()/renderDownloadControls()
// touch, mirroring what cache() would have populated from the real DOM.
[
  "statTarget", "statAccepted", "statDownloadable", "statDownloaded", "statRemaining",
  "statSourceMissing", "statSourceInvalid", "statFound", "statRejected",
  "discoveryBar", "discoveryProgressText", "discoveryPercent", "discoveryStatus",
  "scannedCount", "underCount", "notMovieCount", "wrongLanguageCount", "duplicateCount",
  "apiCount", "retryCount", "currentQuery", "discoveryMessage",
  "downloadStatus", "dashActiveCount", "dashQueuedCount", "dashCompletedCount", "dashFailedCount",
  "downloadSummary", "activeJobsGrid", "heroProgressTitle", "heroProgressSub", "heroProgressCount",
  "heroProgressPct", "heroProgressFill", "heroStatusText", "dashboardStateBanner", "downloadStateBanner",
  "dashDownloadPrimaryBtn", "dashDownloadStopBtn",
  "dgDownloading", "dgDownloadingCount", "dgQueued", "dgQueuedCount",
  "dgCompleted", "dgCompletedCount", "dgFailed", "dgFailedCount", "activityLog",
].forEach((id) => { if (!T.els[id]) T.els[id] = makeEl(); });

T.state.language = "yoruba";
T.state.downloadableOnly = false;

// 17: Discover + Download can show "Discovery complete" while downloads remain active
T.state.mode = "both";
T.els.modeSelect.value = "both";
T.state.target = 10;
T.els.targetInput.value = "10";
T.state.movies = [];
T.state.counts = {
  accepted: 10, downloadable: 10, downloaded: 2, source_missing: 0, source_invalid: 0,
  all: 10, rejected: 0, download: { READY: 3, DOWNLOADED: 2, FAILED: 0 },
};
T.state.runtime = {
  discovery: { status: "COMPLETED" },
  downloads: {
    status: "RUNNING",
    active_jobs: [{ movie_id: 1, worker_id: 0, title: "A", stage: "DOWNLOADING", bytes_downloaded: 100, total_bytes: 1000, speed_bps: 10, eta_seconds: 90 }],
  },
};
T.renderStats(T.state.runtime);
assertEqual(T.els.heroProgressTitle.textContent, "Discovery complete", "discovery-complete title, mode both, downloads still active");
assertEqual(T.els.heroProgressCount.innerHTML.includes("found"), true, "hero count wording says 'found'");
assertEqual(T.els.heroProgressCount.innerHTML.includes("ready"), false, "hero count no longer says misleading 'ready'");
assertEqual(T.els.heroProgressSub.textContent, "Downloads are still running below.", "sub-text makes clear downloads are not done");

T.state.counts.download = { READY: 0, DOWNLOADED: 10, FAILED: 0 };
T.state.runtime.downloads = { status: "IDLE", active_jobs: [] };
T.renderStats(T.state.runtime);
assertEqual(T.els.heroProgressSub.textContent, "All movies found and downloaded.", "sub-text once discovery and downloads both finish");

// 18/19/20/21: Dashboard download Pause/Resume/Stop, and Dashboard vs Downloads page use the same state
T.state.runtime.downloads = { status: "RUNNING", network_wait: false, active_jobs: [] };
T.renderDownloadControls();
assertEqual(T.els.dashDownloadPrimaryBtn.textContent, "Pause downloads", "dashboard download Pause label is explicit");
assertEqual(T.els.downloadPrimaryBtn.textContent, "Pause", "Downloads-page label is unchanged");
assertEqual(T.els.dashDownloadStopBtn.classList.contains("hidden"), false, "dashboard shows Stop downloads while running");

T.state.runtime.downloads = { status: "PAUSED" };
T.renderDownloadControls();
assertEqual(T.els.dashDownloadPrimaryBtn.textContent, "Resume downloads", "dashboard download Resume label is explicit");

T.state.runtime.downloads = { status: "IDLE" };
T.renderDownloadControls();
assertEqual(T.els.dashDownloadPrimaryBtn.textContent, "Start downloads", "dashboard download idle label");
assertEqual(T.els.dashDownloadStopBtn.classList.contains("hidden"), true, "dashboard hides Stop downloads when idle");

["RUNNING", "PAUSED", "WAITING", "ERROR", "IDLE"].forEach((status) => {
  T.state.runtime.downloads = { status };
  T.renderDownloadControls();
  assertEqual(T.els.dashDownloadPrimaryBtn.disabled, T.els.downloadPrimaryBtn.disabled, `Dashboard and Downloads page agree on disabled state for ${status}`);
  assertEqual(T.els.dashDownloadStopBtn.classList.contains("hidden"), T.els.downloadStopBtn.classList.contains("hidden"), `Dashboard and Downloads page agree on Stop visibility for ${status}`);
});

// 22/27: download_progress updates an existing Downloads row live, and shows
// "Downloading" instead of a stale "Queued" once a transfer has begun
T.state.movies = [{ id: 42, title: "Movie A", download_status: "READY", downloadable: true }];
T.state.runtime.downloads = {
  status: "RUNNING",
  active_jobs: [{ movie_id: 42, worker_id: 0, title: "Movie A", stage: "QUEUED", bytes_downloaded: 0, total_bytes: null, speed_bps: 0, eta_seconds: null }],
};
T.renderDownloadsPage(T.state.movies);
assertEqual(T.els.dgDownloading.innerHTML.includes("Queued"), true, "before any progress, a freshly claimed job shows Queued");

T.applyDownloadProgress({ movie_id: 42, bytes_downloaded: 500000, total_bytes: 1000000, speed_bps: 200000, eta_seconds: 5 });
assertEqual(T.els.dgDownloading.innerHTML.includes("Downloading"), true, "download_progress moves the row's stage to Downloading");
assertEqual(T.els.dgDownloading.innerHTML.includes("Queued"), false, "row no longer shows stale Queued once progress arrives");
assertEqual(T.els.dgDownloading.innerHTML.includes("50%"), true, "download_progress updates the row's percentage live");

// 23: download_stage updates the row without reload
T.applyDownloadStage({ movie_id: 42, stage: "MERGING" });
assertEqual(T.els.dgDownloading.innerHTML.includes("Combining video and audio"), true, "download_stage moves the row to the MERGING stage live");

// 24/26: a DOWNLOADED movie renders in Completed and out of Downloading, counts update
T.state.runtime.downloads = { status: "RUNNING", active_jobs: [] };
T.state.movies = [{ id: 42, title: "Movie A", download_status: "DOWNLOADED", file_path: "C:/Movies/Movie A.mp4" }];
T.renderDownloadsPage(T.state.movies);
assertEqual(T.els.dgCompleted.innerHTML.includes("Movie A"), true, "completed movie renders in the Completed group");
assertEqual(T.els.dgDownloading.innerHTML.includes("Movie A"), false, "completed movie is no longer in the Downloading group");
assertEqual(T.els.dgCompletedCount.textContent, "1", "Completed count updates live");

// 25: a FAILED movie renders in Could not download
T.state.movies = [{ id: 42, title: "Movie A", download_status: "FAILED", download_error: "Network error" }];
T.renderDownloadsPage(T.state.movies);
assertEqual(T.els.dgFailed.innerHTML.includes("Movie A"), true, "failed movie renders in the Could not download group");
assertEqual(T.els.dgFailedCount.textContent, "1", "Could not download count updates live");

// 26: queue counts update live across multiple movies
T.state.movies = [
  { id: 1, title: "A", download_status: "READY", downloadable: true },
  { id: 2, title: "B", download_status: "READY", downloadable: true },
  { id: 3, title: "C", download_status: "DOWNLOADED" },
];
T.renderDownloadsPage(T.state.movies);
assertEqual(T.els.dgQueuedCount.textContent, "2", "Queued count reflects two READY movies");
assertEqual(T.els.dgCompletedCount.textContent, "1", "Completed count reflects one DOWNLOADED movie");

// 28/29/30: Live Log progress dedup -- identical consecutive lines are suppressed,
// meaningfully different ones (37%->38%) still get through, and fresh entries still log
T.state.runtime.downloads = { active_jobs: [{ movie_id: 99, title: "Test Movie" }] };
T.progressLogState[99] = { time: Date.now() - 3000, pct: 37, text: "↓ Test Movie — 37% — 101.1 KB/s" };
const beforeDup = T.els.activityLog.children.length;
T.logDownloadProgress({ movie_id: 99, bytes_downloaded: 370, total_bytes: 1000, speed_bps: 103526 });
assertEqual(T.els.activityLog.children.length, beforeDup, "identical consecutive progress line is not appended twice");

T.progressLogState[99].time = Date.now() - 3000;
T.logDownloadProgress({ movie_id: 99, bytes_downloaded: 380, total_bytes: 1000, speed_bps: 103526 });
assertEqual(T.els.activityLog.children.length, beforeDup + 1, "a meaningful change (37%->38%) still appears in the Live Log");

delete T.progressLogState[123];
T.state.runtime.downloads = { active_jobs: [{ movie_id: 123, title: "Fresh Movie" }] };
const beforeFresh = T.els.activityLog.children.length;
T.logDownloadProgress({ movie_id: 123, bytes_downloaded: 10, total_bytes: 100, speed_bps: 5000 });
assertEqual(T.els.activityLog.children.length, beforeFresh + 1, "the Dashboard Live Log still logs fresh download progress");

if (failures > 0) {
  console.error(`\n${failures} assertion(s) failed`);
  process.exit(1);
}
console.log("\nAll dashboard-control JS assertions passed.");
