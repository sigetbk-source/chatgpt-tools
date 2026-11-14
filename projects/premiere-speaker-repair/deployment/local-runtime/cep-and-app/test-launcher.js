"use strict";
const assert = require("assert");
const EventEmitter = require("events");
const {launchStudio} = require("./studio-launcher");

function scenario(probes, outcome) {
  const calls = [], events = [];
  const cp = {
    execFile(exe, args, options, cb) {
      calls.push(["probe", exe]);
      assert(options.timeout > 0);
      const bad = probes.shift();
      cb(bad ? new Error("unavailable") : null, "", bad ? "Xcode license required" : "");
    },
    spawn(exe, args, options) {
      calls.push(["spawn", exe]);
      assert.deepStrictEqual(args, ["/case with spaces/launcher.py"]);
      assert.notStrictEqual(options.shell, true);
      const child = new EventEmitter();
      child.pid = outcome === "spawn-error" ? undefined : 4242;
      child.stderr = new EventEmitter(); child.unref = () => {};
      process.nextTick(() => {
        if (outcome === "spawn-error") {
          child.emit("error", new Error("ENOENT")); child.emit("close", -2);
        } else {
          // CEP's Node 12 does not emit a 'spawn' event.
          setImmediate(() => {
            child.stderr.emit("data", "launcher detail");
            child.emit("close", outcome === "error" ? 1 : 0);
          });
        }
      });
      return child;
    }
  };
  launchStudio({childProcess: cp, candidates: ["managed", "system"], launcher: "/case with spaces/launcher.py",
    onStarted: p => events.push(["started", p]), onError: e => events.push(["error", e]), onClosed: () => events.push(["closed"])});
  return new Promise(resolve => setImmediate(() => setImmediate(() => resolve({calls, events}))));
}
(async () => {
  let s = await scenario([false], "ok");
  assert.deepStrictEqual(s.events, [["started", "managed"], ["closed"]]);
  assert(!s.calls.some(c => c[1] === "system"));
  s = await scenario([true, false], "ok");
  assert.deepStrictEqual(s.events, [["started", "system"], ["closed"]]);
  s = await scenario([true, true], "ok");
  assert.strictEqual(s.events.length, 1); assert(s.events[0][1].includes("Xcode license"));
  assert(!s.calls.some(c => c[0] === "spawn"));
  s = await scenario([false], "spawn-error");
  assert.strictEqual(s.events.length, 1); assert(s.events[0][1].includes("ENOENT"));
  s = await scenario([false], "error");
  assert.strictEqual(s.events.length, 2); assert(s.events[1][1].includes("launcher detail"));
  console.log("PASS: managed Python, fallback, unavailable Python, spawn failure, runtime failure, clean exit");
})().catch(e => {console.error(e); process.exitCode = 1;});
