// Plain-Node behavioural test for the Dashboard Live Log, the exact-progress
// display and the per-language UI state in static/app.js. Loads the real
// shipped file into a vm sandbox with a small DOM stub (same approach as
// test_dashboard_controls.js).
//
// Run with: node tests/test_live_log.js

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function makeEl() {
  let _text = "", _html = "";
  const el = {
    value: "", disabled: false, checked: false, dataset: {}, className: "", style: {}, _kids: [],
    scrollHeight: 0, scrollTop: 0, clientHeight: 0,
    get textContent() { return _text; }, set textContent(v) { _text = String(v); },
    get innerHTML() { return _html; },
    set innerHTML(v) { _html = String(v); if (_html === "") el._kids.length = 0; },
    get children() { return el._kids; },
    get firstChild() { return el._kids[0]; },
    get lastChild() { return el._kids[el._kids.length - 1]; },
    classList: {
      _set: new Set(),
      toggle(c, f) { if (f === undefined) { this._set.has(c) ? this._set.delete(c) : this._set.add(c); } else if (f) this._set.add(c); else this._set.delete(c); return this._set.has(c); },
      add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); }, contains(c) { return this._set.has(c); },
    },
    addEventListener() {},
    appendChild(child) { child._parent = el; el._kids.push(child); return child; },
    prepend(child) { el._kids.unshift(child); },
    removeChild(child) { el._kids.splice(el._kids.indexOf(child), 1); },
    querySelector() { return null; }, closest() { return null; },
  };
  return el;
}
function makeLine() {
  const n = { className: "", innerHTML: "", dataset: {}, remove() { if (n._parent) n._parent.removeChild(n); } };
  return n;
}

const fetchCalls = [];
let fetchEntries = [];
const sandbox = {
  document: { addEventListener() {}, getElementById: () => makeEl(), querySelectorAll: () => [], createElement: makeLine, body: makeEl() },
  window: {}, navigator: { onLine: true }, console,
  fetch: async (url) => { fetchCalls.push(url); return { ok: true, json: async () => ({ entries: fetchEntries }) }; },
  EventSource: class { addEventListener() {} },
  setInterval() {}, setTimeout() {}, clearTimeout() {},
};
vm.createContext(sandbox);
const src = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
const trailer = `
this.__TEST__ = { state, els, appendLogLine, addLog, pushLogEntry, syncLog, renderLogDiagnostics,
  progressPercent, discoveryFor, downloadsFor, deriveDiscoveryPhase, renderDashboardControls,
  renderStats, LOG_MAX, runtimeStatusLabel };
`;
vm.runInContext(src + trailer, sandbox, { filename: "app.js" });
const T = sandbox.__TEST__;

let failures = 0;
function eq(actual, expected, msg) {
  if (actual !== expected) { failures++; console.error(`FAIL: ${msg} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`); }
  else console.log(`ok: ${msg}`);
}

// Every element the renderers touch (taken from the real cache() list).
const ids = /function cache\(\)\{\[(.*?)\]\.forEach/s.exec(src)[1].match(/"([^"]+)"/g).map((s) => s.slice(1, -1));
ids.forEach((id) => { T.els[id] = makeEl(); });
T.els.downloadQuality = makeEl(); T.els.bulkDownloadSelected = makeEl(); T.els.logDiagnostics = makeEl();
T.els.targetInput.value = "1000"; T.els.modeSelect.value = "discover";
T.state.language = "yoruba"; T.state.target = 1000; T.state.downloadableOnly = false;
const log = T.els.activityLog;

// ---- Live Log -----------------------------------------------------------------
T.appendLogLine({ id: 1, at: "2026-10-02T07:31:02Z", level: "info", message: "Searching YouTube for Yoruba movies: old Yoruba film" });
T.appendLogLine({ id: 2, at: "2026-10-02T07:31:04Z", level: "good", message: "✓ Accepted: Movie ABC — 1h 42m" });
T.appendLogLine({ id: 3, at: "2026-10-02T07:31:05Z", level: "bad", message: "Discovery stopped by an error: boom" });
T.appendLogLine({ id: 4, at: "2026-10-02T07:31:06Z", level: "dim", message: "✕ Duplicate: Movie XYZ" });
eq(log.children.length, 4, "four lines are shown");
eq(log.children[0].innerHTML.includes("Searching YouTube"), true, "oldest line is first (chronological, newest at the bottom)");
eq(log.children[3].innerHTML.includes("Duplicate: Movie XYZ"), true, "newest line is last");
eq(/log-time">\d\d:\d\d:\d\d</.test(log.children[1].innerHTML), true, "every line carries a HH:MM:SS timestamp");
eq(log.children[1].className, "log-line log-good", "accepted line is green");
eq(log.children[2].className, "log-line log-bad", "error line is red");
eq(log.children[3].className, "log-line log-dim", "rejection line is muted");

T.appendLogLine({ id: 4, message: "✕ Duplicate: Movie XYZ" });
T.appendLogLine({ id: 2, message: "late copy of an old line" });
eq(log.children.length, 4, "already-seen ids are not shown twice");
T.appendLogLine({ message: "✕ Duplicate: Movie XYZ" });
eq(log.children.length, 4, "an identical line is never repeated back to back");
T.addLog("Bulk remove: updated 3", "log-good");
eq(log.children.length, 5, "client-side addLog still appends");

// ---- fixed height + auto-follow ----------------------------------------------------
log.scrollHeight = 1000; log.clientHeight = 260; log.scrollTop = 740;       // reading the bottom
T.appendLogLine({ id: 10, message: "follow me" });
eq(log.scrollTop, 1000, "stays pinned to the newest line while the user is at the bottom");
log.scrollTop = 100;                                                         // scrolled up to read
T.appendLogLine({ id: 11, message: "do not yank the view" });
eq(log.scrollTop, 100, "does not scroll away from a user who scrolled up");

// ---- bounded -----------------------------------------------------------------------
log.innerHTML = ""; T.state.logLastId = 0;
for (let i = 1; i <= T.LOG_MAX + 40; i++) T.appendLogLine({ id: i, message: `line ${i}` });
eq(log.children.length, T.LOG_MAX, "the log never grows past its cap");
eq(log.children[0].innerHTML.includes(`line 41<`), true, "oldest lines are dropped first");

// ---- language filter + catch-up after reload -----------------------------------------
log.innerHTML = ""; T.state.logLastId = 0; T.state.language = "igbo";
T.pushLogEntry({ id: 1, language: "yoruba", message: "Yoruba line" });
T.pushLogEntry({ id: 2, language: "igbo", message: "Igbo line" });
T.pushLogEntry({ id: 3, language: null, message: "Shared line" });
eq(log.children.length, 2, "another language's log lines are not shown");
fetchEntries = [{ id: 7, language: "igbo", message: "history one" }, { id: 8, language: "igbo", message: "history two" }];
(async () => {
  await T.syncLog(true);
  eq(log.children.length, 2, "a reload / language switch refills the log from the server");
  eq(fetchCalls[fetchCalls.length - 1].includes("language=igbo"), true, "the history request is for the selected language");
  eq(fetchCalls[fetchCalls.length - 1].includes("after=0"), true, "a reset starts from the beginning");

  // ---- exact progress -----------------------------------------------------------------
  eq(T.progressPercent(951, 952) < 100, true, "951/952 is below 100%");
  eq(Math.floor(T.progressPercent(951, 952)), 99, "951/952 reads as 99%, never 100%");
  eq(T.progressPercent(999, 1000), 99.9, "999/1000 reads 99.9%");
  eq(T.progressPercent(1000, 1000), 100, "100% only when the target is really reached");
  eq(T.progressPercent(0, 0), 0, "no target means 0%");

  T.state.language = "yoruba"; T.state.target = 952; T.els.targetInput.value = "952";
  T.state.counts = { accepted: 951, downloadable: 951, downloaded: 951, all: 1524, rejected: 573, download: {} };
  T.state.runtime = { discovery: { status: "IDLE", language: "yoruba", stats: {} }, downloads: { status: "IDLE", language: "yoruba" } };
  T.renderStats(T.state.runtime);
  eq(T.els.heroProgressPct.textContent, "99%", "Dashboard hero shows 99% for 951 of 952");
  eq(T.els.heroProgressCount.innerHTML.includes("951"), true, "hero shows 951 found");

  T.state.counts.accepted = 952; T.state.counts.downloadable = 952;
  T.renderStats(T.state.runtime);
  eq(T.els.heroProgressPct.textContent, "100%", "hero shows 100% once 952 of 952 is reached");

  // ---- stop reasons are visible on the Dashboard --------------------------------------
  T.state.target = 1000; T.els.targetInput.value = "1000"; T.state.counts.accepted = 951;
  const exhausted = "Search pool exhausted. Found 951 of 1000. No additional unique qualifying movies were found.";
  T.state.runtime = { discovery: { status: "EXHAUSTED", language: "yoruba", message: exhausted, stats: { candidates_scanned: 400, duplicates_skipped: 300, rejected_wrong_language: 40, api_requests: 9 } }, downloads: { status: "IDLE", language: "yoruba" } };
  T.renderStats(T.state.runtime);
  eq(T.els.heroProgressSub.textContent, exhausted, "the hero explains why discovery stopped short");
  eq(T.els.heroProgressTitle.textContent, "Search pool exhausted", "the hero does not call it complete");
  eq(T.els.heroProgressPct.textContent, "95%", "951 of 1000 reads 95%");
  eq(T.els.discoveryStatus.textContent, "Search pool exhausted", "status pill says exhausted, not Completed");
  eq(T.deriveDiscoveryPhase(), "continue", "an exhausted run below target offers Find more movies");
  T.renderDashboardControls();
  eq(T.els.primaryActionBtn.textContent, "Find more movies", "button reads Find more movies");
  eq(T.els.primaryActionBtn.disabled, false, "and it is enabled");
  eq(T.els.logDiagnostics.textContent.includes("400 scanned"), true, "diagnostics line shows what was scanned");
  eq(T.els.logDiagnostics.textContent.includes("300 duplicates"), true, "diagnostics line shows duplicates");

  T.state.runtime.discovery = { status: "ERROR", language: "yoruba", message: "YouTube's daily search quota is used up.", stats: {} };
  T.renderStats(T.state.runtime);
  eq(T.els.heroProgressSub.textContent, "YouTube's daily search quota is used up.", "an error reason is shown on the Dashboard");

  // ---- language isolation in the UI -------------------------------------------------------
  T.state.language = "igbo"; T.state.counts.accepted = 0; T.state.target = 1000;
  T.state.runtime = { discovery: { status: "EXHAUSTED", language: "yoruba", message: exhausted, stats: { candidates_scanned: 400 } }, downloads: { status: "RUNNING", language: "yoruba", active_jobs: [{ movie_id: 1, language: "yoruba" }] } };
  eq(T.discoveryFor(T.state.runtime).status, "IDLE", "Yoruba's discovery result is not shown in Igbo mode");
  eq(T.downloadsFor(T.state.runtime).status, "IDLE", "Yoruba's downloads are not shown in Igbo mode");
  T.renderStats(T.state.runtime);
  eq(T.els.heroProgressPct.textContent, "0%", "Igbo shows its own 0% while Yoruba holds 951");
  T.state.language = "yoruba";
  eq(T.discoveryFor(T.state.runtime).status, "EXHAUSTED", "switching back shows Yoruba's own state again");
  T.renderDashboardControls();
  eq(T.els.languageSelect.disabled, true, "language switching is locked while a download worker is busy");
  T.state.runtime.downloads = { status: "IDLE", language: "yoruba" };
  T.renderDashboardControls();
  eq(T.els.languageSelect.disabled, false, "language switching is allowed again when everything is idle");

  if (failures > 0) { console.error(`\n${failures} assertion(s) failed`); process.exit(1); }
  console.log("\nAll Live Log / language-state JS assertions passed.");
})();
