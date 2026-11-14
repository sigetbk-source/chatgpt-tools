"use strict";

// Use the same managed Python as the desktop application before trying macOS's
// developer-tool shim. Probe each interpreter instead of assuming it can run.
function launchStudio(options) {
  var cp = options.childProcess;
  var candidates = options.candidates.slice();
  var failures = [];
  var finished = false;
  function fail(message) {
    if (finished) return;
    finished = true;
    options.onError(message);
  }
  function probe() {
    if (!candidates.length) {
      fail("制作ツールを起動できるPythonがありません。\n" + failures.join("\n"));
      return;
    }
    var python = candidates.shift();
    cp.execFile(python, ["-c", "import sys,json,pathlib,subprocess; assert sys.version_info >= (3, 10)"],
      {timeout: 5000, maxBuffer: 8192}, function (error, stdout, stderr) {
        if (error) {
          failures.push(python + ": " + String(stderr || error.message).trim().slice(0, 600));
          probe();
          return;
        }
        var child;
        var detail = "";
        try {
          child = cp.spawn(python, [options.launcher], {
            detached: true, stdio: ["ignore", "ignore", "pipe"]
          });
          if (child.stderr) child.stderr.on("data", function (chunk) {
            detail = (detail + String(chunk)).slice(-8192);
          });
          child.once("error", function (err) { fail("制作ツールの起動に失敗しました: " + err.message); });
          // Older CEP Node runtimes have no ChildProcess 'spawn' event.
          // A successful synchronous spawn supplies a pid; launch errors are
          // emitted on the next tick and must still reach the error handler.
          process.nextTick(function () {
            if (!finished && typeof child.pid === "number") options.onStarted(python);
          });
          child.once("close", function (code, signal) {
            if (finished) return;
            if (code !== 0) {
              fail("制作ツールが停止しました（" + (signal || code) + "）。\n" + detail.trim());
            } else {
              finished = true;
              options.onClosed();
            }
          });
          child.unref();
        } catch (err) { fail("制作ツールの起動に失敗しました: " + err.message); }
      });
  }
  probe();
}

module.exports = {launchStudio: launchStudio};
