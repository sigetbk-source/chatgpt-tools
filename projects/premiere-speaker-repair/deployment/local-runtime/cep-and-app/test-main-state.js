"use strict";
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(__dirname + "/main.js", "utf8");
const start = source.indexOf("  function openStudio() {");
const end = source.indexOf("  function checkRevisionQueue", start);
function setup() {
  const launches = [], timers = [];
  const button = {disabled: false};
  const context = {
    fs: {existsSync: () => true}, path: require("path"), os: require("os"),
    runtimeLauncher: "/local/narration/launcher.py", localRuntime: "/local", childProcess: {},
    studioLauncher: {launchStudio: options => launches.push(options)},
    studioStarting: false, studioLaunchSerial: 0,
    pendingCaseStartedAt: 0, activeManifest: {caseName: "previous"},
    status: {textContent: ""}, now: 1000,
    Date: {now: () => context.now},
    document: {getElementById: () => button},
    window: {setTimeout: fn => timers.push(fn)},
    clearManifest: () => {context.activeManifest = null;},
    showManifest: value => {context.activeManifest = value;}
  };
  vm.createContext(context); vm.runInContext(source.slice(start, end), context);
  return {context, launches, timers, button};
}
// An old child's error/close/timeout must not unlock or alter a later launch.
{
  const {context: c, launches: l, timers: t, button} = setup();
  c.openStudio(); l[0].onStarted(); t[0]();
  c.now = 2000; c.openStudio();
  const status = c.status.textContent;
  l[0].onError("old error"); l[0].onClosed(); t[0]();
  assert.strictEqual(button.disabled, true);
  assert.strictEqual(c.studioStarting, true);
  assert.strictEqual(c.status.textContent, status);
  l[1].onStarted();
  assert.strictEqual(c.pendingCaseStartedAt, 2000);
  l[0].onError("old error after newer start");
  assert.strictEqual(c.pendingCaseStartedAt, 2000);
  assert(!c.status.textContent.includes("old error"));
}
// A cancelled launch restores that project's previous approved manifest.
{
  const {context: c, launches: l} = setup();
  c.openStudio(); l[0].onStarted(); l[0].onClosed();
  assert.strictEqual(c.pendingCaseStartedAt, 0);
  assert.strictEqual(c.activeManifest.caseName, "previous");
}
// Preflight can take seconds: exports before actual start are not new approval.
{
  const {context: c, launches: l} = setup();
  c.openStudio(); c.now = 9000; l[0].onStarted();
  assert.strictEqual(c.pendingCaseStartedAt, 9000,
    "approval cutoff must be actual child start, not beginning of slow interpreter probes");
}
console.log("PASS: launch identity, cancellation restoration, preflight approval cutoff");
