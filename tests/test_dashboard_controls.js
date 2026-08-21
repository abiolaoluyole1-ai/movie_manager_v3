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
  return {
    value: "", textContent: "", innerHTML: "", disabled: false, checked: false,
    dataset: {}, className: "", style: {},
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
    appendChild() {}, querySelector() { return null; }, closest() { return null; },
  };
}

const elementRegistry = {};
function getElementById(id) {
  if (!elementRegistry[id]) elementRegistry[id] = makeEl();
  return elementRegistry[id];
}

const documentStub = {
  addEventListener() {}, getElementById, querySelectorAll() { return []; },
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
T.els.providerSelect = makeEl();
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

if (failures > 0) {
  console.error(`\n${failures} assertion(s) failed`);
  process.exit(1);
}
console.log("\nAll dashboard-control JS assertions passed.");
