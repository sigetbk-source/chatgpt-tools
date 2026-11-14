from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = ROOT / "work" / "uxp"
STATE_PATH = WORK_ROOT / "jobs.json"
UDT_APP = Path("/Applications/Adobe UXP Developer Tools/Adobe UXP Developer Tools.app")
UDT_ELECTRON = UDT_APP / "Contents/MacOS/Adobe UXP Developer Tools"
LOADER = ROOT / "scripts" / "uxp_auto_loader.js"


class WritebackError(RuntimeError):
    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


def _bridge_request(path: str, payload: dict | None = None) -> dict:
    request = Request(
        f"http://127.0.0.1:8878{path}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError) as error:
        raise WritebackError("UXP連携サーバーが起動していません", "bridge_unavailable") from error
    if not result.get("ok"):
        raise WritebackError(str(result.get("error") or "UXP連携処理に失敗しました"))
    return result


def _port_is_open(port: int = 14001) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def ensure_udt_running(timeout: float = 25.0) -> None:
    if not UDT_APP.exists() or not UDT_ELECTRON.exists():
        raise WritebackError("Adobe UXP Developer Toolがインストールされていません", "udt_missing")
    if not _port_is_open():
        subprocess.run(["open", "-a", str(UDT_APP)], check=True, capture_output=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_is_open():
            return
        time.sleep(0.25)
    raise WritebackError(
        "UXP Developer Toolを起動しましたが、Premiereとの接続を確認できませんでした",
        "udt_not_connected",
    )


def _load_job(job_id: str) -> dict:
    try:
        jobs = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        job = jobs[job_id]
    except (OSError, KeyError, json.JSONDecodeError) as error:
        raise WritebackError("書き戻し対象の案件情報が見つかりません") from error
    return job


def _project_name(job: dict) -> str:
    if job.get("project_name"):
        return str(job["project_name"])
    try:
        config = json.loads(Path(job["config_path"]).read_text(encoding="utf-8"))
        return str(config.get("project_name") or "").split("｜", 1)[0]
    except (OSError, KeyError, json.JSONDecodeError):
        return ""


def _plugin_source(spec: dict) -> str:
    encoded = json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
    return r'''const ppro = require("premierepro");
const uxp = require("uxp");
const spec = __SPEC__;

async function fileEntry(path, create) {
  const fs = uxp.storage.localFileSystem;
  const normalized = String(path).replace(/\\/g, "/");
  if (!create) return await fs.getEntryWithUrl("file:" + normalized);
  const slash = normalized.lastIndexOf("/");
  const folder = await fs.getEntryWithUrl("file:" + normalized.slice(0, slash));
  return await folder.createFile(normalized.slice(slash + 1), {overwrite: true});
}
async function readText(path) { return await (await fileEntry(path, false)).read({format: uxp.storage.formats.utf8}); }
async function writeText(path, text) { await (await fileEntry(path, true)).write(text, {append: false}); }
function normalizedPath(path) { return String(path || "").normalize("NFC"); }
async function asFolder(item) {
  try { const folder = ppro.FolderItem.cast(item); return {folder, children: await folder.getItems()}; }
  catch (_) { return null; }
}
async function findClip(items) {
  for (const item of items) {
    if (String(item.getId()) === String(spec.assetId)) {
      try {
        const clip = ppro.ClipProjectItem.cast(item);
        if (normalizedPath(await clip.getMediaFilePath()) === normalizedPath(spec.mediaPath)) return clip;
      } catch (_) {}
    }
    const folder = await asFolder(item);
    if (folder) { const found = await findClip(folder.children); if (found) return found; }
  }
  return null;
}
function fingerprint(raw) {
  const value = typeof raw === "string" ? JSON.parse(raw) : raw;
  const speakers = new Map((value.speakers || []).map(item => [String(item.id), String(item.name || "")]));
  return JSON.stringify((value.segments || []).map(segment => ({
    start: Number(segment.start), duration: Number(segment.duration),
    speaker: speakers.get(String(segment.speaker)) || String(segment.speaker || ""),
    text: (segment.words || []).map(word => String(word.text || word.word || "")).join("")
  })));
}
async function finish(result) { await writeText(spec.resultPath, JSON.stringify(result)); }
async function run() {
  let backupWritten = false;
  let imported = false;
  try {
    const project = await ppro.Project.getActiveProject();
    if (!project) {
      await finish({ok:false, code:"project_not_open", message:`書き出し元のPremiereプロジェクト「${spec.projectName}」を開いてから、もう一度押してください。`});
      return;
    }
    const actualGuid = String(project.guid || "");
    const actualName = String(project.name || "");
    if ((spec.projectGuid && actualGuid !== String(spec.projectGuid)) || (spec.projectName && actualName !== spec.projectName)) {
      await finish({ok:false, code:"project_mismatch", expectedProject:spec.projectName, actualProject:actualName,
        message:`書き出し元は「${spec.projectName}」です。現在開いている「${actualName || "名称不明"}」を閉じ、書き出し元プロジェクトを開いてください。`});
      return;
    }
    if (spec.projectPath && normalizedPath(project.path || "") !== normalizedPath(spec.projectPath)) {
      await finish({ok:false, code:"project_mismatch", expectedProject:spec.projectName, actualProject:actualName,
        message:`開いているプロジェクトの保存場所が書き出し元と異なります。書き出し元「${spec.projectPath}」を開いてください。`});
      return;
    }
    const root = ppro.FolderItem.cast(await project.getRootItem());
    const clip = await findClip(await root.getItems());
    if (!clip) {
      await finish({ok:false, code:"asset_mismatch", message:`対象素材「${spec.assetName}」のIDと保存場所が書き出し時と一致しません。`});
      return;
    }
    const corrected = await readText(spec.correctedPath);
    const parsed = JSON.parse(corrected);
    if (!Array.isArray(parsed.segments) || !parsed.segments.length || !Array.isArray(parsed.speakers)) throw new Error("修正版JSONの構造が不正です");
    const expected = fingerprint(parsed);
    try {
      const original = await ppro.Transcript.exportToJSON(clip);
      if (spec.sourceFingerprint && fingerprint(original) !== spec.sourceFingerprint) {
        await finish({ok:false, code:"source_changed", message:"Premiere上の文字起こしが書き出し時から変わっています。現在の文字起こしを取り直して修正案を作り直してください。"});
        return;
      }
      await writeText(spec.backupPath, original);
      backupWritten = true;
    } catch (backupError) {
      if (!spec.allowBackupExportFailure) throw backupError;
    }
    const importedSegments = ppro.Transcript.importFromJSON(corrected);
    let committed = false;
    project.lockedAccess(() => {
      const action = ppro.Transcript.createImportTextSegmentsAction(importedSegments, clip);
      committed = project.executeTransaction(compound => compound.addAction(action), "修正済み素材文字起こしを反映");
    });
    if (!committed) throw new Error("Premiereが文字起こしの変更を受け付けませんでした");
    imported = true;
    const saved = await project.save();
    if (!saved) throw new Error("Premiereプロジェクトを保存できませんでした");
    await new Promise(resolve => setTimeout(resolve, 600));
    try {
      const actual = fingerprint(await ppro.Transcript.exportToJSON(clip));
      if (actual !== expected) throw new Error("反映後の内容が修正版JSONと一致しません");
      await finish({ok:true, code:"confirmed", saved:true, verified:true, backupPath:spec.backupPath,
        message:`${spec.assetName}への書き戻し・一致確認・保存が完了しました。`});
    } catch (verificationError) {
      await finish({ok:true, code:"pending_verification", saved:true, verified:false, backupPath:spec.backupPath,
        message:`${spec.assetName}へ反映して保存しました。Premiere再読込後に一致確認してください。`, detail:String(verificationError.message || verificationError)});
    }
  } catch (error) {
    await finish({ok:false, code: imported ? "writeback_error_after_import" : "writeback_error", backupWritten,
      backupPath: backupWritten ? spec.backupPath : "", message:`書き戻しに失敗しました: ${error.message || error}`});
  }
}
module.exports = {commands:{run}};
run().catch(error => finish({ok:false, code:"unexpected_error", message:String(error.stack || error)}));
'''.replace("__SPEC__", encoded)


def inspect_active_project() -> dict:
    """Read active Premiere project identity without changing project state."""
    ensure_udt_running()
    with tempfile.TemporaryDirectory(prefix="premiere-project-inspect-") as directory:
        plugin = Path(directory)
        result_path = plugin / "result.json"
        spec = json.dumps({"resultPath": str(result_path)}, ensure_ascii=False)
        source = r'''const ppro = require("premierepro");
const uxp = require("uxp");
const spec = __SPEC__;
async function writeResult(value) {
  const normalized = spec.resultPath.replace(/\\/g, "/");
  const slash = normalized.lastIndexOf("/");
  const folder = await uxp.storage.localFileSystem.getEntryWithUrl("file:" + normalized.slice(0, slash));
  const file = await folder.createFile(normalized.slice(slash + 1), {overwrite:true});
  await file.write(JSON.stringify(value), {append:false});
}
async function run() {
  const project = await ppro.Project.getActiveProject();
  if (!project) return await writeResult({ok:false, code:"project_not_open", message:"開いているPremiereプロジェクトがありません"});
  await writeResult({ok:true, projectName:String(project.name || ""), projectGuid:String(project.guid || ""), projectPath:String(project.path || "")});
}
module.exports = {commands:{run}};
run().catch(error => writeResult({ok:false, code:"inspect_error", message:String(error.message || error)}));
'''.replace("__SPEC__", spec)
        manifest = {
            "manifestVersion": 5,
            "id": f"jp.tbk.premiere-project-inspect-{uuid.uuid4().hex}",
            "name": "Premiere Project Inspection",
            "version": "1.0.0",
            "main": "index.js",
            "host": [{"app": "premierepro", "minVersion": "25.6.0"}],
            "requiredPermissions": {"localFileSystem": "fullAccess"},
            "entrypoints": [{"type": "command", "id": "run", "label": "プロジェクト確認"}],
        }
        (plugin / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (plugin / "index.js").write_text(source, encoding="utf-8")
        environment = os.environ.copy()
        environment["ELECTRON_RUN_AS_NODE"] = "1"
        completed = subprocess.run(
            [str(UDT_ELECTRON), str(LOADER), str(plugin / "manifest.json"), str(result_path), ""],
            text=True,
            capture_output=True,
            env=environment,
            timeout=75,
        )
        if not result_path.is_file():
            detail = (completed.stderr or completed.stdout or "Premiereから応答がありません").strip()
            raise WritebackError(detail, "uxp_unavailable")
        return json.loads(result_path.read_text(encoding="utf-8"))


def run_browser_writeback(job_id: str) -> dict:
    job = _load_job(job_id)
    project_name = _project_name(job)
    corrected_path = Path(job.get("corrected_json") or "")
    if not corrected_path.is_file():
        raise WritebackError("先に修正内容を保存してください")
    ensure_udt_running()
    backup_dir = Path(job["config_path"]).parent / "input" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"premiere-transcript-before-browser-writeback-{stamp}.json"
    with tempfile.TemporaryDirectory(prefix="premiere-browser-writeback-") as directory:
        plugin = Path(directory)
        result_path = plugin / "result.json"
        spec = {
            "projectGuid": str(job.get("project_guid") or ""),
            "projectName": project_name,
            "projectPath": str(job.get("project_path") or ""),
            "assetId": str(job.get("asset_id") or ""),
            "assetName": str(job.get("asset_name") or ""),
            "mediaPath": str(job.get("media_path") or ""),
            "correctedPath": str(corrected_path),
            "backupPath": str(backup_path),
            "resultPath": str(result_path),
            "sourceFingerprint": str(job.get("source_fingerprint") or ""),
        }
        manifest = {
            "manifestVersion": 5,
            "id": f"jp.tbk.premiere-browser-writeback-{uuid.uuid4().hex}",
            "name": "Premiere Browser Transcript Writeback",
            "version": "1.0.0",
            "main": "index.js",
            "host": [{"app": "premierepro", "minVersion": "25.6.0"}],
            "requiredPermissions": {"localFileSystem": "fullAccess"},
            "entrypoints": [{"type": "command", "id": "run", "label": "文字起こしを書き戻す"}],
        }
        (plugin / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (plugin / "index.js").write_text(_plugin_source(spec), encoding="utf-8")
        environment = os.environ.copy()
        environment["ELECTRON_RUN_AS_NODE"] = "1"
        completed = subprocess.run(
            [
                str(UDT_ELECTRON),
                str(LOADER),
                str(plugin / "manifest.json"),
                str(result_path),
                project_name,
            ],
            text=True,
            capture_output=True,
            env=environment,
            timeout=75,
        )
        if not result_path.is_file():
            detail = (completed.stderr or completed.stdout or "Premiereから応答がありません").strip()
            raise WritebackError(detail, "uxp_unavailable")
        result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("backupPath") and Path(result["backupPath"]).is_file():
        _bridge_request(
            f"/api/jobs/{job_id}/writeback-backup",
            {"json": Path(result["backupPath"]).read_text(encoding="utf-8")},
        )
    code = result.get("code")
    if code == "confirmed":
        _bridge_request(f"/api/jobs/{job_id}/writeback-complete", {"saved": True})
    elif code == "pending_verification":
        _bridge_request(f"/api/jobs/{job_id}/writeback-pending-verification", {"error": result.get("detail", "再読込後の検証が必要です")})
    elif code not in {"premiere_not_running", "project_not_open", "project_mismatch", "asset_mismatch"}:
        _bridge_request(f"/api/jobs/{job_id}/writeback-failed", {"error": result.get("message", "書き戻し失敗"), "restored": False})
    return result


def run_browser_restore(job_id: str) -> dict:
    job = _load_job(job_id)
    project_name = _project_name(job)
    restore_path = Path(job.get("backup_path") or "")
    if not restore_path.is_file():
        raise WritebackError("復旧用バックアップが見つかりません")
    _bridge_request(f"/api/jobs/{job_id}/request-restore")
    ensure_udt_running()
    backup_dir = Path(job["config_path"]).parent / "input" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    current_state_path = backup_dir / f"premiere-transcript-empty-state-before-restore-{stamp}.json"
    with tempfile.TemporaryDirectory(prefix="premiere-browser-restore-") as directory:
        plugin = Path(directory)
        result_path = plugin / "result.json"
        spec = {
            "projectGuid": str(job.get("project_guid") or ""),
            "projectName": project_name,
            "projectPath": str(job.get("project_path") or ""),
            "assetId": str(job.get("asset_id") or ""),
            "assetName": str(job.get("asset_name") or ""),
            "mediaPath": str(job.get("media_path") or ""),
            "correctedPath": str(restore_path),
            "backupPath": str(current_state_path),
            "resultPath": str(result_path),
            "allowBackupExportFailure": True,
        }
        manifest = {
            "manifestVersion": 5,
            "id": f"jp.tbk.premiere-browser-restore-{uuid.uuid4().hex}",
            "name": "Premiere Browser Transcript Restore",
            "version": "1.0.0",
            "main": "index.js",
            "host": [{"app": "premierepro", "minVersion": "25.6.0"}],
            "requiredPermissions": {"localFileSystem": "fullAccess"},
            "entrypoints": [{"type": "command", "id": "run", "label": "文字起こしを復旧する"}],
        }
        (plugin / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (plugin / "index.js").write_text(_plugin_source(spec), encoding="utf-8")
        environment = os.environ.copy()
        environment["ELECTRON_RUN_AS_NODE"] = "1"
        completed = subprocess.run(
            [
                str(UDT_ELECTRON),
                str(LOADER),
                str(plugin / "manifest.json"),
                str(result_path),
                project_name,
            ],
            text=True,
            capture_output=True,
            env=environment,
            timeout=75,
        )
        if not result_path.is_file():
            detail = (completed.stderr or completed.stdout or "Premiereから応答がありません").strip()
            raise WritebackError(detail, "uxp_unavailable")
        result = json.loads(result_path.read_text(encoding="utf-8"))
    code = result.get("code")
    if code == "confirmed":
        _bridge_request(f"/api/jobs/{job_id}/restore-complete", {"saved": True})
    elif code not in {
        "pending_verification", "premiere_not_running", "project_not_open",
        "project_mismatch", "asset_mismatch",
    }:
        _bridge_request(
            f"/api/jobs/{job_id}/restore-failed",
            {"error": result.get("message", "復旧失敗")},
        )
    return {**result, "restoredFrom": str(restore_path), "currentStateBackup": str(current_state_path)}
