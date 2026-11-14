#!/usr/bin/env python3
"""案件内に保存するナレーション制作ジョブ。

ブラウザはジョブを承認するだけで、Premiereの変更は行わない。
Premiere 側（UXP）が result/premiere_manifest.json を読み、反映結果をここへ返す。
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1
INDEX_PATH = Path(os.environ.get("NARRATION_JOB_INDEX", str(Path.home() / "Library" / "Application Support" / "ナレーション制作ツール" / "narration-job-index.json")))


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, default: dict | None = None) -> dict:
    try:
        if not path.exists():
            return default or {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default or {}
    return value if isinstance(value, dict) else (default or {})


def create(case_folder: Path, project: Path, mode: str, script: Path | None = None) -> Path:
    """新しい操作単位を作る。既存の案件データそのものは複製しない。"""
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    job = case_folder / ".premiere-tools" / "jobs" / job_id
    (job / "input").mkdir(parents=True, exist_ok=False)
    (job / "result").mkdir()
    context = {
        "schemaVersion": SCHEMA_VERSION,
        "jobId": job_id,
        "operation": "narration",
        "caseFolder": str(case_folder),
        "projectPath": str(project),
        "mode": mode,
        "scriptPath": str(script) if script else "",
        "createdAt": now(),
    }
    status = {
        "schemaVersion": SCHEMA_VERSION,
        "jobId": job_id,
        "operation": "narration",
        "state": "editing",
        "message": "ブラウザで内容を確認・編集してください。",
        "createdAt": context["createdAt"],
        "updatedAt": context["createdAt"],
    }
    write_json(job / "context.json", context)
    write_json(job / "status.json", status)
    index = read_json(INDEX_PATH, {"schemaVersion": SCHEMA_VERSION, "jobs": []})
    entries = [value for value in index.get("jobs", []) if isinstance(value, dict) and value.get("path") != str(job)]
    entries.append({"id": job_id, "path": str(job), "caseFolder": str(case_folder), "updatedAt": context["createdAt"]})
    index["schemaVersion"] = SCHEMA_VERSION
    index["jobs"] = entries[-200:]
    write_json(INDEX_PATH, index)
    return job


def update(job: Path, state: str, message: str, **extra: object) -> dict:
    status_path = job / "status.json"
    status = read_json(status_path)
    status.update({"state": state, "message": message, "updatedAt": now(), **extra})
    write_json(status_path, status)
    index = read_json(INDEX_PATH, {"schemaVersion": SCHEMA_VERSION, "jobs": []})
    for entry in index.get("jobs", []):
        if isinstance(entry, dict) and entry.get("path") == str(job):
            entry["updatedAt"] = status["updatedAt"]
    write_json(INDEX_PATH, index)
    return status


def summary(job: Path) -> dict:
    required = [job / "status.json", job / "context.json", job / "result" / "premiere_manifest.json"]
    offline = [path for path in required if path.exists() and getattr(path.stat(), "st_flags", 0) & 0x40000000]
    if offline:
        case_folder = job.parents[2]
        return {
            "id": job.name, "operation": "narration", "state": "offline",
            "message": "iCloud未ダウンロードの案件です。Finderでダウンロードしてから開いてください。",
            "caseFolder": str(case_folder), "caseName": case_folder.name,
            "updatedAt": "", "resultReady": False, "manifestPath": "",
        }
    status = read_json(job / "status.json")
    context = read_json(job / "context.json")
    result = read_json(job / "result" / "premiere_manifest.json")
    return {
        "id": status.get("jobId") or context.get("jobId") or job.name,
        "operation": "narration",
        "state": status.get("state", "unknown"),
        "message": status.get("message", ""),
        "caseFolder": context.get("caseFolder", ""),
        "caseName": Path(str(context.get("caseFolder", "案件"))).name,
        "mode": context.get("mode", ""),
        "updatedAt": status.get("updatedAt", ""),
        "browserUrl": status.get("browserUrl", ""),
        "transcriptState": status.get("transcriptState", ""),
        "transcriptError": status.get("transcriptError", ""),
        "captionState": status.get("captionState", ""),
        "captionSrtPath": status.get("captionSrtPath", ""),
        "captionAppliedAt": status.get("captionAppliedAt", ""),
        "diffPlanId": status.get("diffPlanId", ""),
        "resultReady": bool(result),
        "manifestPath": str(job / "result" / "premiere_manifest.json") if result else "",
    }


def list_jobs(case_folder: Path | None = None) -> list[dict]:
    jobs: list[dict] = []
    if case_folder:
        roots = [case_folder / ".premiere-tools" / "jobs"]
        paths = []
        for root in roots:
            try:
                if root.is_dir():
                    paths.extend(path for path in root.iterdir() if path.is_dir())
            except OSError:
                continue
    else:
        index = read_json(INDEX_PATH, {"jobs": []})
        paths = [Path(str(entry.get("path", ""))) for entry in index.get("jobs", []) if isinstance(entry, dict)]
    for path in paths:
        try:
            if not path.is_dir():
                continue
            value = summary(path)
        except OSError:
            continue
        if value.get("state") == "unknown" and not value.get("caseFolder"):
            continue
        jobs.append(value)
    return sorted(jobs, key=lambda value: str(value.get("updatedAt", "")), reverse=True)


def find(job_id: str) -> Path:
    index = read_json(INDEX_PATH, {"jobs": []})
    for entry in index.get("jobs", []):
        if isinstance(entry, dict) and str(entry.get("id")) == job_id:
            path = Path(str(entry.get("path", "")))
            if path.is_dir():
                return path
    raise ValueError("ナレーションジョブが見つかりません")
