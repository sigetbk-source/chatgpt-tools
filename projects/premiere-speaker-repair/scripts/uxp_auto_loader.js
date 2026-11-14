const fs = require("fs");

const asarRoot = "/Applications/Adobe UXP Developer Tools/Adobe UXP Developer Tools.app/Contents/Resources/app.asar";
const PluginMgr = require(
  asarRoot + "/node_modules/@adobe/uxp-devtools-core/src/core/client/PluginMgr.js"
);

global.UxpLogger = {
  log: (...args) => console.log(...args),
  error: (...args) => console.error(...args),
  warn: (...args) => console.warn(...args),
  verbose: (...args) => console.log(...args),
};

const manifest = process.argv[2];
const resultPath = process.argv[3];
const expectedProjectName = process.argv[4] || "書き出し元プロジェクト";
if (!manifest || !resultPath) throw new Error("manifest path and result path are required");

async function run() {
  const manager = new PluginMgr();
  manager.registerHostAppLogListener((_event, data) => {
    const message = data && data.message ? data.message : "";
    if (message.includes("[console]")) console.log(message);
  });
  await manager.connectToService(14001);
  await new Promise(resolve => setTimeout(resolve, 600));
  const apps = manager.getConnectedApps();
  if (!apps.some(app => app.id === "premierepro")) {
    fs.writeFileSync(resultPath, JSON.stringify({
      ok: false,
      code: "premiere_not_running",
      message: `Premiere Proで書き出し元のプロジェクト「${expectedProjectName}」を開いてから、もう一度押してください。`,
    }));
    manager.disconnect();
    return;
  }
  await manager.loadPlugin({
    manifest,
    apps: [],
    breakOnStart: false,
    isPlaygroundPlugin: false,
  });
  for (let index = 0; index < 240 && !fs.existsSync(resultPath); index += 1) {
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  manager.disconnect();
  if (!fs.existsSync(resultPath)) {
    throw new Error("Premiereから60秒以内に結果が返りませんでした");
  }
}

run().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
