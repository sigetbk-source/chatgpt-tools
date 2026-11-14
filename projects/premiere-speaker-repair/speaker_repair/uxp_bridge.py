from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .audio import convert_audio, prepare_review_video
from .corrections import build_correction_candidates, build_term_audit, export_corrected_csv
from .manifest import write_manifest
from .narration_sync import synchronize_narration_srt
from .project import DEFAULT_MODEL_CACHE_DIR, Project, OfflineFileError, read_local_text
from .references import build_references
from .speaker_registry import add_speaker_names
from .whisper_transcribe import transcribe


ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = ROOT / "work" / "uxp"
STATE_PATH = WORK_ROOT / "jobs.json"
LOG_ROOT = WORK_ROOT / "logs"
UXP_LOG_PATH = LOG_ROOT / "uxp-errors.jsonl"
SERVER_LOG_PATH = LOG_ROOT / "server-errors.jsonl"
IPC_ROOT = WORK_ROOT / "ipc"
IPC_INBOX = IPC_ROOT / "inbox"
IPC_OUTBOX = IPC_ROOT / "outbox"
NARRATION_RUNTIME = Path.home() / "Library" / "Application Support" / "ナレーション制作ツール" / "runtime"
if not NARRATION_RUNTIME.is_dir():
    NARRATION_RUNTIME = ROOT.parent / "ナレーションPremiereハブ_作業" / "runtime"
REFERENCE_SUFFIXES = {".docx", ".pdf", ".txt", ".md", ".csv"}
VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v", ".mxf", ".avi"}
LOG_LOCK = threading.RLock()
MAX_LOG_TEXT = 20_000


def _safe_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def discover_reference_documents(folder: str | None) -> list[Path]:
    if not folder:
        return []
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise ValueError(f"案件資料フォルダが見つかりません: {root}")
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in REFERENCE_SUFFIXES
    )


def load_case_glossary(reference_dir: str | None) -> dict:
    if not reference_dir:
        return {"glossary_terms": [], "glossary_aliases": {}}
    path = WORK_ROOT / "case-glossaries" / f"{_safe_id(str(Path(reference_dir).expanduser()))}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"glossary_terms": [], "glossary_aliases": {}}
    terms = [str(term) for term in payload.get("glossary_terms", []) if str(term).strip()]
    aliases = {
        str(canonical): [str(value) for value in values if str(value).strip()]
        for canonical, values in payload.get("glossary_aliases", {}).items()
        if isinstance(values, list)
    }
    return {"glossary_terms": terms, "glossary_aliases": aliases}


def write_diagnostic_log(payload: dict, path: Path = UXP_LOG_PATH) -> dict:
    """Append one bounded, local-only diagnostic record as JSON Lines."""
    if not isinstance(payload, dict):
        raise ValueError("ログ内容が不正です")
    record = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "source": str(payload.get("source") or "uxp")[:80],
        "level": str(payload.get("level") or "error")[:20],
        "context": str(payload.get("context") or "unknown")[:200],
        "message": str(payload.get("message") or "")[:MAX_LOG_TEXT],
        "stack": str(payload.get("stack") or "")[:MAX_LOG_TEXT],
        "plugin_version": str(payload.get("plugin_version") or "")[:40],
        "host": str(payload.get("host") or "")[:300],
        "client_time": str(payload.get("client_time") or "")[:80],
        "details": payload.get("details") if isinstance(payload.get("details"), dict) else {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOG_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return record


def latest_diagnostic_logs(path: Path = UXP_LOG_PATH, limit: int = 100) -> list[dict]:
    if not path.exists():
        return []
    with LOG_LOCK:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 500)):]
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def wait_for_review_proxy(
    project: Project,
    timeout_seconds: float = 7200,
    poll_seconds: float = 1.0,
    stable_checks: int = 3,
) -> Path:
    """Wait until AME's MP4 exists and its size has stopped changing."""
    deadline = time.monotonic() + timeout_seconds
    previous_size = -1
    stable_count = 0
    while time.monotonic() < deadline:
        try:
            size = project.review_video_path.stat().st_size
        except OSError:
            size = 0
        if size > 32_768 and size == previous_size:
            stable_count += 1
            if stable_count >= stable_checks:
                return project.review_video_path
        else:
            stable_count = 0
        previous_size = size
        time.sleep(poll_seconds)
    raise TimeoutError("Media Encoderの確認動画が時間内に完成しませんでした")


def transcript_has_words(transcript: object) -> bool:
    """Reject Premiere placeholder exports that contain only an empty duration segment."""
    if not isinstance(transcript, dict):
        return False
    for segment in transcript.get("segments") or []:
        for word in segment.get("words") or []:
            if str(word.get("text") or word.get("word") or "").strip():
                return True
    return False


def transcript_fingerprint(transcript: dict) -> str:
    speakers = {
        str(item.get("id")): str(item.get("name") or "")
        for item in transcript.get("speakers", [])
    }
    semantic = [
        {
            "start": float(segment.get("start", 0)),
            "duration": float(segment.get("duration", 0)),
            "speaker": speakers.get(
                str(segment.get("speaker")), str(segment.get("speaker") or "")
            ),
            "text": "".join(
                str(word.get("text") or word.get("word") or "")
                for word in segment.get("words", [])
            ),
        }
        for segment in transcript.get("segments", [])
    ]
    return json.dumps(semantic, ensure_ascii=False, separators=(",", ":"))


def prepare_inputs_parallel(project: Project, external_review_proxy: bool = False) -> dict:
    """Build references, analysis audio and a review proxy concurrently per asset."""
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="asset-preparation") as executor:
        references_future = executor.submit(build_references, project)
        audio_future = executor.submit(convert_audio, project)
        video_future = executor.submit(
            wait_for_review_proxy if external_review_proxy else prepare_review_video,
            project,
        )
        references = references_future.result()
        audio_future.result()
        video_future.result()
    return references


class JobManager:
    def __init__(self) -> None:
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transcript-job")
        self.jobs: dict[str, dict] = self._load()
        self.review_processes: dict[str, subprocess.Popen] = {}

    def _load(self) -> dict[str, dict]:
        if not STATE_PATH.exists():
            return {}
        try:
            jobs = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        for job in jobs.values():
            if job.get("status") in {"queued", "proxy_queued", "preparing", "whisper"}:
                job["status"] = "stopped"
                job["message"] = "連携サーバーが終了したため停止しました。再実行してください。"
        return jobs

    def _save(self) -> None:
        STATE_PATH.write_text(
            json.dumps(self.jobs, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def list_jobs(self) -> list[dict]:
        with self.lock:
            changed = False
            offline_views = {}
            for job in self.jobs.values():
                try:
                    project = Project.load(job["config_path"])
                    transcript = json.loads(read_local_text(project.transcript_json))
                    if not transcript_has_words(transcript):
                        if job.get("status") != "invalid_transcript":
                            job.update(
                                status="invalid_transcript",
                                message="Premiere文字起こし本文なし・選択の再読込が必要",
                            )
                            changed = True
                        continue
                    ready = json.loads(read_local_text(project.writeback_ready_path))
                    corrected_json = Path(ready["corrected_json"])
                except OfflineFileError as error:
                    offline_views[id(job)] = {
                        **job, "status": "offline", "source_status": job.get("status"),
                        "message": str(error),
                    }
                    continue
                except (OSError, KeyError, ValueError, json.JSONDecodeError):
                    continue
                marker_mtime = project.writeback_ready_path.stat().st_mtime
                completed_at = float(job.get("completed_at", 0) or 0)
                eligible = job.get("status") in {
                    "review_ready", "confirmed", "writeback_failed", "restore_failed", "restored"
                }
                if corrected_json.is_file() and eligible and marker_mtime > completed_at:
                    job.update(
                        status="ready_to_writeback",
                        message="確認確定済み・Premiereへ書き戻せます",
                        corrected_json=str(corrected_json),
                        patch_count=int(ready.get("patch_count", 0)),
                    )
                    changed = True
            if changed:
                self._save()
            return sorted((offline_views.get(id(job), job) for job in self.jobs.values()), key=lambda item: item.get("created_at", 0))

    def reset_jobs(self) -> int:
        """Archive test job folders and clear the visible queue without touching source media."""
        with self.lock:
            jobs = list(self.jobs.values())
            stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
            archive_root = WORK_ROOT / "archive" / stamp
            for job in jobs:
                process = self.review_processes.pop(job["id"], None)
                if process and process.poll() is None:
                    process.terminate()
                job_dir = WORK_ROOT / job["id"]
                if job_dir.exists():
                    archive_root.mkdir(parents=True, exist_ok=True)
                    destination = archive_root / job["id"]
                    if destination.exists():
                        shutil.rmtree(destination)
                    shutil.move(str(job_dir), str(destination))
            self.jobs = {}
            self._save()
            return len(jobs)

    def submit_batch(self, payload: dict) -> list[dict]:
        reference_dir = payload.get("reference_dir")
        references = discover_reference_documents(reference_dir)
        case_glossary = load_case_glossary(reference_dir)
        assets = payload.get("assets") or []
        if not assets:
            raise ValueError("対象素材がありません")
        project_identity = payload.get("project_path") or payload.get("project_guid") or payload.get("project_name") or "Premiere"
        speaker_registry_path = WORK_ROOT / "speaker-registries" / f"{_safe_id(project_identity)}.json"
        add_speaker_names(
            speaker_registry_path,
            [
                speaker.get("name")
                for asset in assets
                for speaker in (asset.get("transcript") or {}).get("speakers", [])
            ],
        )
        submitted = []
        for asset in assets:
            media_path = Path(asset.get("path", "")).expanduser()
            if not media_path.is_file():
                raise ValueError(f"素材ファイルが見つかりません: {media_path}")
            transcript = asset.get("transcript")
            if not transcript_has_words(transcript):
                raise ValueError(
                    f"{asset.get('name', media_path.name)} のPremiere文字起こしに本文がありません。"
                    "文字起こし完了後に選択を読み込み直してください"
                )
            project_key = payload.get("project_path") or payload.get("project_guid", "")
            key = _safe_id(f"{project_key}:{asset['id']}")
            with self.lock:
                existing = self.jobs.get(key)
                active_statuses = {
                    "queued", "awaiting_proxy", "proxy_queued", "preparing", "whisper",
                    "aligning", "writing_back",
                }
                if existing and existing.get("status") in active_statuses:
                    submitted.append(existing.copy())
                    continue
                if existing:
                    try:
                        existing_project = Project.load(existing["config_path"])
                        existing_transcript = json.loads(existing_project.transcript_json.read_text(encoding="utf-8"))
                    except (OSError, KeyError, ValueError, json.JSONDecodeError):
                        existing_transcript = None
                    if transcript_has_words(existing_transcript):
                        submitted.append(existing.copy())
                        continue
                    process = self.review_processes.pop(key, None)
                    if process and process.poll() is None:
                        process.terminate()
            job_dir = WORK_ROOT / key
            if job_dir.exists():
                stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
                archive_dir = WORK_ROOT / "archive" / f"invalid-transcript-{stamp}"
                archive_dir.mkdir(parents=True, exist_ok=True)
                destination = archive_dir / key
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.move(str(job_dir), str(destination))
            input_dir = job_dir / "input"
            analysis_dir = job_dir / "analysis"
            input_dir.mkdir(parents=True, exist_ok=True)
            transcript_path = input_dir / "premiere-transcript.json"
            transcript_path.write_text(
                json.dumps(transcript, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            speakers = [
                item.get("name") for item in transcript.get("speakers", []) if item.get("name")
            ]
            config = {
                "project_name": f"{payload.get('project_name', 'Premiere')}｜{asset.get('name', media_path.name)}",
                "global_glossary": str(ROOT / "projects" / "global-glossary.json"),
                "glossary_terms": case_glossary["glossary_terms"],
                "glossary_aliases": case_glossary["glossary_aliases"],
                "source_dir": str(input_dir),
                "transcript_json": transcript_path.name,
                "source_media": str(media_path),
                "reference_documents": [str(path) for path in references],
                "known_speakers": speakers,
                "speaker_registry_path": str(speaker_registry_path),
                "cast": {},
                "analysis_dir": str(analysis_dir),
                "model_cache_dir": str(DEFAULT_MODEL_CACHE_DIR),
                "transcript_alignment": "time",
                "minimum_chunk_seconds": 2.5,
                "maximum_chunk_seconds": 10.0,
            }
            config_path = job_dir / "project.json"
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            proxy_mode = (
                "ame"
                if asset.get("proxy_mode") == "ame" and media_path.suffix.lower() in VIDEO_SUFFIXES
                else "direct"
            )
            review_proxy_path = analysis_dir / "review-proxy.mp4"
            (analysis_dir / "writeback-ready.json").unlink(missing_ok=True)
            if proxy_mode == "ame":
                review_proxy_path.unlink(missing_ok=True)
            job = {
                "id": key,
                "asset_id": asset["id"],
                "asset_name": asset.get("name", media_path.name),
                "media_path": str(media_path),
                "project_guid": payload.get("project_guid", ""),
                "project_name": payload.get("project_name", ""),
                "project_path": payload.get("project_path", ""),
                "source_fingerprint": transcript_fingerprint(transcript),
                "reference_dir": reference_dir,
                "reference_count": len(references),
                "config_path": str(config_path),
                "proxy_mode": proxy_mode,
                "review_proxy_path": str(review_proxy_path),
                "status": "awaiting_proxy" if proxy_mode == "ame" else "queued",
                "message": "Media Encoder投入待ち" if proxy_mode == "ame" else "解析待ち",
                "created_at": time.time(),
            }
            with self.lock:
                self.jobs[key] = job
                self._save()
            submitted.append(job.copy())
            if proxy_mode != "ame":
                self.executor.submit(self._run, key)
        return submitted

    def proxy_queued(self, key: str) -> dict:
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise ValueError("対象ジョブが見つかりません")
            if job.get("status") != "awaiting_proxy":
                return job.copy()
            job.update(status="proxy_queued", message="Media Encoderで確認動画作成中")
            self._save()
            result = job.copy()
        self.executor.submit(self._run, key)
        return result

    def proxy_error(self, key: str, error: str) -> dict:
        """Record AME failure and continue with the existing local converter."""
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise ValueError("対象ジョブが見つかりません")
            if job.get("status") != "awaiting_proxy":
                return job.copy()
            job.update(
                status="queued",
                message="Media Encoder失敗・予備変換で継続",
                proxy_mode="fallback",
                proxy_error=str(error)[:2000],
            )
            self._save()
            result = job.copy()
        write_diagnostic_log(
            {
                "source": "uxp-encoder",
                "context": f"job:{key}",
                "message": str(error),
                "details": {"job_id": key, "fallback": True},
            },
            SERVER_LOG_PATH,
        )
        self.executor.submit(self._run, key)
        return result

    def _update(self, key: str, status: str, message: str, **extra) -> None:
        with self.lock:
            self.jobs[key].update(status=status, message=message, **extra)
            self._save()

    def _run(self, key: str) -> None:
        try:
            job = self.jobs[key]
            project = Project.load(job["config_path"])
            self._update(key, "preparing", "Premiere文字起こしを整理中")
            write_manifest(project)
            self._update(key, "preparing", "辞書作成・音声・確認動画を並行処理中")
            references = prepare_inputs_parallel(
                project,
                external_review_proxy=job.get("proxy_mode") == "ame",
            )
            self._update(
                key,
                "whisper",
                "Whisper文字起こし中",
                reference_terms=len(references["terms"]),
            )
            transcribe(project)
            self._update(key, "aligning", "修正候補を作成中")
            candidates = build_correction_candidates(project)
            audit = build_term_audit(project)
            self._update(
                key,
                "review_ready",
                "手動確認できます",
                candidate_count=len(candidates["items"]),
                review_count=sum(bool(item["needs_review"]) for item in candidates["items"]),
                unresolved_terms=len(audit["unresolved_candidates"]),
            )
        except Exception as error:  # surfaced verbatim in the local-only test panel
            write_diagnostic_log(
                {
                    "source": "bridge-job",
                    "context": f"job:{key}",
                    "message": f"{type(error).__name__}: {error}",
                    "stack": traceback.format_exc(),
                    "details": {"job_id": key},
                },
                SERVER_LOG_PATH,
            )
            self._update(key, "error", f"{type(error).__name__}: {error}")

    def open_review(self, key: str) -> str:
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise ValueError("対象ジョブが見つかりません")
            if job.get("status") not in {"review_ready", "ready_to_writeback", "confirmed"}:
                raise ValueError("まだ確認画面を開けません")
            process = self.review_processes.get(key)
            port = 8900 + (int(key[:4], 16) % 500)
            if not process or process.poll() is not None:
                process = subprocess.Popen(
                    [sys.executable, "-m", "speaker_repair.transcript_server", job["config_path"], "--port", str(port)],
                    cwd=ROOT,
                )
                self.review_processes[key] = process
        url = f"http://127.0.0.1:{port}/"
        webbrowser.open(url)
        return url

    def corrected_json(self, key: str) -> str:
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise ValueError("対象ジョブが見つかりません")
        project = Project.load(job["config_path"])
        result = export_corrected_csv(project)
        json_path = result.get("json")
        if not json_path:
            raise ValueError(result.get("json_warning", "修正済みJSONを作れませんでした"))
        return Path(json_path).read_text(encoding="utf-8")

    def request_writeback(self, key: str) -> dict:
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise ValueError("対象ジョブが見つかりません")
        project = Project.load(job["config_path"])
        try:
            ready = json.loads(project.writeback_ready_path.read_text(encoding="utf-8"))
            corrected_json = Path(ready["corrected_json"])
        except (OSError, KeyError, json.JSONDecodeError) as error:
            raise ValueError("先に確認内容を保存してください") from error
        if not corrected_json.is_file():
            raise ValueError("修正済みJSONが見つかりません")
        self._update(
            key,
            "writeback_requested",
            "Premiereへ書き戻し要求中",
            corrected_json=str(corrected_json),
            writeback_requested_at=time.time(),
            verified=False,
            saved=False,
            completed_at=0,
            verification_error="",
        )
        return self.jobs[key].copy()

    def writeback_started(self, key: str) -> dict:
        self._update(key, "writing_back", "Premiereへ書き戻し中")
        return self.jobs[key].copy()

    def save_writeback_backup(self, key: str, transcript_json: str) -> str:
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise ValueError("対象ジョブが見つかりません")
        parsed = json.loads(transcript_json)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("segments"), list):
            raise ValueError("バックアップ用文字起こしJSONが不正です")
        backup_dir = Path(job["config_path"]).parent / "input" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = backup_dir / f"premiere-transcript-before-writeback-{stamp}.json"
        backup_path.write_text(transcript_json, encoding="utf-8")
        self._update(key, "writing_back", "バックアップ完了・書き戻し中", backup_path=str(backup_path))
        return str(backup_path)

    def writeback_complete(self, key: str, payload: dict) -> dict:
        self._update(
            key,
            "confirmed",
            "Premiereへ書き戻し・一致確認・保存完了",
            verified=True,
            saved=bool(payload.get("saved")),
            completed_at=time.time(),
        )
        return self.jobs[key].copy()

    def writeback_pending_verification(self, key: str, error: str) -> dict:
        self._update(
            key,
            "applied_pending_verification",
            "Premiere反映・保存済み／プロジェクト再読込後の一致確認待ち",
            verification_error=str(error)[:2000],
            applied_at=time.time(),
            verified=False,
            saved=True,
        )
        return self.jobs[key].copy()

    def acknowledge_writeback(self, key: str, note: str) -> dict:
        self._update(
            key,
            "confirmed",
            "Premiere反映済み（ユーザー確認）",
            verified=True,
            verification_note=str(note)[:1000],
            completed_at=time.time(),
        )
        return self.jobs[key].copy()

    def writeback_failed(self, key: str, error: str, restored: bool = False) -> dict:
        self._update(
            key,
            "writeback_failed",
            f"書き戻し失敗: {str(error)[:500]}",
            writeback_error=str(error)[:2000],
            restored=bool(restored),
        )
        return self.jobs[key].copy()

    def request_restore(self, key: str) -> dict:
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise ValueError("対象ジョブが見つかりません")
            backup_path = Path(job.get("backup_path", ""))
        if not backup_path.is_file():
            raise ValueError("復旧用バックアップが見つかりません")
        self._update(key, "restore_requested", "元の文字起こしを復旧待ち")
        return self.jobs[key].copy()

    def restore_json(self, key: str) -> str:
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise ValueError("対象ジョブが見つかりません")
            backup_path = Path(job.get("backup_path", ""))
        return backup_path.read_text(encoding="utf-8")

    def restore_complete(self, key: str, saved: bool) -> dict:
        self._update(
            key,
            "restored",
            "元の文字起こしの復旧・一致確認・保存完了",
            restored=True,
            restore_saved=bool(saved),
            restored_at=time.time(),
        )
        return self.jobs[key].copy()

    def restore_failed(self, key: str, error: str) -> dict:
        self._update(
            key,
            "restore_failed",
            f"自動復旧失敗: {str(error)[:500]}",
            restore_error=str(error)[:2000],
        )
        return self.jobs[key].copy()


MANAGER = JobManager()


def narration_jobs_module():
    """ナレーション実装をUXPブリッジのジョブ一覧へ接続する。"""
    if str(NARRATION_RUNTIME) not in sys.path:
        sys.path.insert(0, str(NARRATION_RUNTIME))
    import narration_jobs
    return narration_jobs


def narration_job(job_id: str) -> tuple[object, Path]:
    jobs = narration_jobs_module()
    return jobs, jobs.find(job_id)


def narration_diff_plan(job_id: str, script_path: str) -> dict:
    if str(NARRATION_RUNTIME) not in sys.path:
        sys.path.insert(0, str(NARRATION_RUNTIME))
    import diff_update
    jobs, job_path = narration_job(job_id)
    manifest = json.loads((job_path / "result" / "premiere_manifest.json").read_text(encoding="utf-8"))
    project = Path(str(manifest.get("projectPath", "")))
    if not (project / "narration.json").is_file():
        raise ValueError("この承認済みナレーションの管理JSONが見つかりません")
    plan = diff_update.create_plan(project, Path(script_path).expanduser().resolve())
    status = jobs.read_json(job_path / "status.json")
    jobs.update(
        job_path,
        status.get("state", "applied"),
        status.get("message", ""),
        diffPlanId=plan.get("updateId", ""),
        diffPlanPath=str(
            project / ".premiere-tools" / "narration-updates" /
            str(plan.get("updateId", "")) / "plan.json"
        ),
    )
    return plan


def equivalent_wav_files(first_path: str, second_path: str) -> bool:
    """同名の旧WAVを利用してよいか、内容のハッシュまで照合する。"""
    first = Path(first_path)
    second = Path(second_path)
    if not first.is_file() or not second.is_file() or first.suffix.lower() != ".wav" or second.suffix.lower() != ".wav":
        return False
    if first.stat().st_size != second.stat().st_size:
        return False
    first_hash = hashlib.sha256()
    second_hash = hashlib.sha256()
    with first.open("rb") as first_file, second.open("rb") as second_file:
        while True:
            first_chunk = first_file.read(1024 * 1024)
            second_chunk = second_file.read(1024 * 1024)
            if first_chunk != second_chunk:
                return False
            if not first_chunk:
                break
            first_hash.update(first_chunk)
            second_hash.update(second_chunk)
    return first_hash.digest() == second_hash.digest()


def open_narration_studio() -> dict:
    launcher = NARRATION_RUNTIME / "launcher.py"
    if not launcher.is_file():
        raise ValueError("ナレーション制作ランタイムが見つかりません")
    subprocess.Popen(
        [sys.executable, str(launcher)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"opened": True, "message": "案件フォルダの選択画面を開きました。"}


def open_narration_diff_review(job_path: Path, project: Path, update_id: str) -> dict:
    """生成済みの変更・追加音声だけをブラウザ確認画面で開く。"""
    server = NARRATION_RUNTIME / "project_server.py"
    if not server.is_file():
        raise ValueError("ナレーション確認画面のランタイムが見つかりません")
    if not project.is_dir() or not (project / "narration.json").is_file():
        raise ValueError("ナレーション管理データが見つかりません")
    if not update_id or Path(update_id).name != update_id:
        raise ValueError("差分更新IDが不正です")
    subprocess.Popen(
        [
            sys.executable,
            str(server),
            str(project),
            "--job-dir",
            str(job_path),
            "--diff-update-id",
            update_id,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"opened": True, "message": "差分音声の確認画面を開きました。"}


def open_narration_audio_revision(job_path: Path, project: Path) -> dict:
    """反映済みの全ナレーションを、読み方・話者・速度の再編集画面で開く。"""
    server = NARRATION_RUNTIME / "project_server.py"
    if not server.is_file():
        raise ValueError("ナレーション確認画面のランタイムが見つかりません")
    if not project.is_dir() or not (project / "narration.json").is_file():
        raise ValueError("ナレーション管理データが見つかりません")
    subprocess.Popen(
        [
            sys.executable,
            str(server),
            str(project),
            "--job-dir",
            str(job_path),
            "--audio-revision",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"opened": True, "message": "生成済み音声の修正画面を開きました。"}


def dispatch_request(path: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    if path == "/api/health":
        return {"ok": True, "version": 3, "transport": "file"}
    if path == "/api/narration/open":
        return {"ok": True, **open_narration_studio()}
    if path == "/api/narration/jobs":
        return {"ok": True, "jobs": narration_jobs_module().list_jobs()}
    if path == "/api/narration/media-equivalent":
        return {"ok": True, "equivalent": equivalent_wav_files(str(payload.get("firstPath", "")), str(payload.get("secondPath", "")))}
    if path.startswith("/api/narration/jobs/") and path.endswith("/manifest"):
        key = path.split("/")[4]
        _, job_path = narration_job(key)
        manifest = json.loads((job_path / "result" / "premiere_manifest.json").read_text(encoding="utf-8"))
        return {"ok": True, "manifest": manifest}
    if path.startswith("/api/narration/jobs/") and path.endswith("/audio-revision-open"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        status = jobs.read_json(job_path / "status.json")
        if status.get("state") not in {"applied", "audio_editing", "audio_update_ready"}:
            raise ValueError("Premiere反映済みの案件だけ音声を修正できます")
        manifest = jobs.read_json(job_path / "result" / "premiere_manifest.json")
        if manifest.get("mode") != "audio":
            raise ValueError("音声を生成していない案件です")
        binding = status.get("premiereBinding", manifest.get("premiereBinding", {}))
        if not isinstance(binding, dict) or not isinstance(binding.get("items"), list) or not binding.get("items"):
            raise ValueError("Premiereへ配置済みの音声情報が見つかりません")
        manifest["premiereBinding"] = binding
        jobs.write_json(job_path / "result" / "premiere_manifest.json", manifest)
        return {"ok": True, **open_narration_audio_revision(
            job_path, Path(str(manifest.get("projectPath", "")))
        )}
    if path.startswith("/api/narration/jobs/") and path.endswith("/audio-update-manifest"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        status = jobs.read_json(job_path / "status.json")
        if status.get("state") != "audio_update_ready":
            raise ValueError("ブラウザで音声修正を承認してください")
        manifest_path = job_path / "result" / "premiere_manifest.json"
        manifest = jobs.read_json(manifest_path)
        manifest["premiereBinding"] = status.get("premiereBinding", manifest.get("premiereBinding", {}))
        manifest["manifestPath"] = str(manifest_path)
        return {"ok": True, "manifest": manifest}
    if path.startswith("/api/narration/jobs/") and path.endswith("/audio-update-complete"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        status = jobs.read_json(job_path / "status.json")
        manifest_path = job_path / "result" / "premiere_manifest.json"
        manifest = jobs.read_json(manifest_path)
        binding = payload.get("premiereBinding")
        if not isinstance(binding, dict) or not isinstance(binding.get("items"), list):
            raise ValueError("Premiere反映後のトラック情報が不正です")
        manifest["premiereBinding"] = binding
        jobs.write_json(manifest_path, manifest)
        return {"ok": True, "job": jobs.update(
            job_path,
            "applied",
            "生成済み音声の修正をPremiereへ反映しました。同期済みSRTを準備します。",
            premiereBinding=binding,
            captionState="requested",
            captionSrtPath=manifest.get("srtPath", ""),
            audioRevisionAppliedAt=datetime.now(timezone.utc).isoformat(),
        )}
    if path.startswith("/api/narration/jobs/") and path.endswith("/diff-plan"):
        key = path.split("/")[4]
        return {"ok": True, "plan": narration_diff_plan(key, str(payload.get("scriptPath", "")))}
    if path.startswith("/api/narration/jobs/") and path.endswith("/diff-latest"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        status = jobs.read_json(job_path / "status.json")
        plan_path = Path(str(status.get("diffPlanPath", "")))
        if not plan_path.is_file():
            raise ValueError("保存済みの差分一覧が見つかりません")
        return {"ok": True, "plan": jobs.read_json(plan_path)}
    if path.startswith("/api/narration/jobs/") and path.endswith("/diff-apply"):
        key = path.split("/")[4]
        if str(NARRATION_RUNTIME) not in sys.path:
            sys.path.insert(0, str(NARRATION_RUNTIME))
        import diff_update
        _, job_path = narration_job(key)
        manifest = json.loads((job_path / "result" / "premiere_manifest.json").read_text(encoding="utf-8"))
        result = diff_update.apply_plan(
            Path(str(manifest["projectPath"])),
            str(payload.get("updateId", "")),
            [int(value) for value in payload.get("selected", [])],
            baseline_manifest=manifest,
        )
        jobs = narration_jobs_module()
        status = jobs.read_json(job_path / "status.json")
        update_manifest = result.get("manifest", {})
        if isinstance(update_manifest, dict):
            update_manifest["premiereBinding"] = status.get("premiereBinding", {})
            update_manifest_path = Path(str(update_manifest.get("manifestPath", "")))
            if update_manifest_path.is_file():
                stored_manifest = dict(update_manifest)
                stored_manifest.pop("manifestPath", None)
                jobs.write_json(update_manifest_path, stored_manifest)
        jobs.update(
            job_path,
            "diff_review",
            "差分音声を生成しました。ブラウザで確認・修正して承認してください。",
            updateId=result.get("updateId", ""),
            updateManifestPath=result.get("manifest", {}).get("manifestPath", ""),
            diffPlanId="",
            diffPlanPath="",
        )
        review = open_narration_diff_review(
            job_path,
            Path(str(manifest["projectPath"])),
            str(result.get("updateId", "")),
        )
        return {"ok": True, "result": result, "review": review}
    if path.startswith("/api/narration/jobs/") and path.endswith("/diff-review-open"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        status = jobs.read_json(job_path / "status.json")
        update_id = str(status.get("updateId", ""))
        if not update_id:
            raise ValueError("確認する差分更新が見つかりません")
        manifest = json.loads((job_path / "result" / "premiere_manifest.json").read_text(encoding="utf-8"))
        result = open_narration_diff_review(
            job_path,
            Path(str(manifest["projectPath"])),
            update_id,
        )
        return {"ok": True, **result}
    if path.startswith("/api/narration/jobs/") and path.endswith("/diff-manifest"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        status = jobs.read_json(job_path / "status.json")
        manifest_path = Path(str(status.get("updateManifestPath", "")))
        if not manifest_path.is_file():
            raise ValueError("Premiere反映用の差分データが見つかりません")
        manifest = jobs.read_json(manifest_path)
        # 差分生成後に既存タイムラインを登録した場合も、
        # 最新のPremiere紐付けを使う。
        manifest["premiereBinding"] = status.get("premiereBinding", manifest.get("premiereBinding", {}))
        manifest["manifestPath"] = str(manifest_path)
        return {"ok": True, "manifest": manifest}
    if path.startswith("/api/narration/jobs/") and path.endswith("/diff-complete"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        status = jobs.read_json(job_path / "status.json")
        update_path = Path(str(status.get("updateManifestPath", "")))
        if not update_path.is_file():
            raise ValueError("差分更新データが見つかりません")
        update_manifest = jobs.read_json(update_path)
        old_manifest_path = job_path / "result" / "premiere_manifest.json"
        old_manifest = jobs.read_json(old_manifest_path)
        binding = payload.get("premiereBinding")
        if not isinstance(binding, dict) or not isinstance(binding.get("items"), list):
            raise ValueError("Premiere反映後のトラック情報が不正です")
        promoted = dict(old_manifest)
        promoted.update({
            "schemaVersion": 3,
            "audioItems": update_manifest.get("audioItems", []),
            "audioBinName": update_manifest.get("audioBinName", old_manifest.get("audioBinName", "")),
            "srtPath": update_manifest.get("srtPath", ""),
            "revision": update_manifest.get("updateId", ""),
            "updatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        })
        jobs.write_json(old_manifest_path, promoted)
        return {"ok": True, "job": jobs.update(
            job_path,
            "applied",
            "差分音声をPremiereへ反映しました。新しいSRTが準備済みです。",
            premiereBinding=binding,
            updateId="",
            updateManifestPath="",
            captionState="requested",
            captionSrtPath=update_manifest.get("srtPath", ""),
            appliedAt=datetime.now(timezone.utc).isoformat(),
        )}
    if path.startswith("/api/narration/jobs/") and path.endswith("/sync-srt"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        status = jobs.read_json(job_path / "status.json")
        manifest_path = job_path / "result" / "premiere_manifest.json"
        if not manifest_path.is_file():
            raise ValueError("ナレーション管理データが見つかりません")
        timings = payload.get("timings")
        if not isinstance(timings, list):
            raise ValueError("Premiere上の音声時刻がありません")
        result = synchronize_narration_srt(manifest_path, timings)
        jobs.update(
            job_path,
            status.get("state", "applied"),
            "音声の実時刻に同期したSRTを準備しました。古いキャプショントラックを削除し、SRTを貼り直してください。",
            captionState="requested",
            captionSrtPath=result["srtPath"],
            srtSynchronizedAt=datetime.now(timezone.utc).isoformat(),
        )
        return {"ok": True, **result}
    if path.startswith("/api/narration/jobs/") and path.endswith("/apply-started"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        return {"ok": True, "job": jobs.update(job_path, "applying", "Premiereへ反映中です。")}
    if path.startswith("/api/narration/jobs/") and path.endswith("/apply-complete"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        return {"ok": True, "job": jobs.update(job_path, "applied", "Premiereへの反映が完了しました。", appliedAt=datetime.now(timezone.utc).isoformat())}
    if path.startswith("/api/narration/jobs/") and path.endswith("/apply-failed"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        return {"ok": True, "job": jobs.update(job_path, "error", "Premiereへの反映に失敗しました。", error=str(payload.get("error", "不明なエラー"))[:2000])}
    if path.startswith("/api/narration/jobs/") and path.endswith("/transcript-complete"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        status = jobs.read_json(job_path / "status.json")
        return {"ok": True, "job": jobs.update(
            job_path, status.get("state", "approved"), "個別WAVへ文字起こしを登録しました。",
            transcriptState="completed", transcriptCompletedAt=datetime.now(timezone.utc).isoformat(), transcriptError=""
        )}
    if path.startswith("/api/narration/jobs/") and path.endswith("/transcript-failed"):
        key = path.split("/")[4]
        jobs, job_path = narration_job(key)
        status = jobs.read_json(job_path / "status.json")
        return {"ok": True, "job": jobs.update(
            job_path, status.get("state", "approved"), "個別WAVへの文字起こし登録に失敗しました。",
            transcriptState="failed", transcriptError=str(payload.get("error", "不明なエラー"))[:2000]
        )}
    if path == "/api/jobs":
        return {"ok": True, "jobs": MANAGER.list_jobs()}
    if path == "/api/logs/latest":
        return {"ok": True, "logs": latest_diagnostic_logs()}
    if path == "/api/logs":
        return {"ok": True, "record": write_diagnostic_log(payload)}
    if path == "/api/reset":
        return {"ok": True, "reset_jobs": MANAGER.reset_jobs()}
    if path == "/api/batch":
        return {"ok": True, "jobs": MANAGER.submit_batch(payload)}
    if path.startswith("/api/jobs/") and path.endswith("/proxy-queued"):
        key = path.split("/")[3]
        return {"ok": True, "job": MANAGER.proxy_queued(key)}
    if path.startswith("/api/jobs/") and path.endswith("/proxy-error"):
        key = path.split("/")[3]
        return {"ok": True, "job": MANAGER.proxy_error(key, payload.get("error", "Media Encoder投入失敗"))}
    if path.startswith("/api/jobs/") and path.endswith("/review"):
        key = path.split("/")[3]
        return {"ok": True, "url": MANAGER.open_review(key)}
    if path.startswith("/api/jobs/") and path.endswith("/corrected-json"):
        key = path.split("/")[3]
        return {"ok": True, "json": MANAGER.corrected_json(key)}
    if path.startswith("/api/jobs/") and path.endswith("/request-writeback"):
        key = path.split("/")[3]
        return {"ok": True, "job": MANAGER.request_writeback(key)}
    if path.startswith("/api/jobs/") and path.endswith("/writeback-started"):
        key = path.split("/")[3]
        return {"ok": True, "job": MANAGER.writeback_started(key)}
    if path.startswith("/api/jobs/") and path.endswith("/writeback-backup"):
        key = path.split("/")[3]
        return {"ok": True, "backup_path": MANAGER.save_writeback_backup(key, payload.get("json", ""))}
    if path.startswith("/api/jobs/") and path.endswith("/writeback-complete"):
        key = path.split("/")[3]
        return {"ok": True, "job": MANAGER.writeback_complete(key, payload)}
    if path.startswith("/api/jobs/") and path.endswith("/writeback-pending-verification"):
        key = path.split("/")[3]
        return {"ok": True, "job": MANAGER.writeback_pending_verification(key, payload.get("error", "再読込後の検証が必要です"))}
    if path.startswith("/api/jobs/") and path.endswith("/acknowledge-writeback"):
        key = path.split("/")[3]
        return {"ok": True, "job": MANAGER.acknowledge_writeback(key, payload.get("note", "ユーザーがPremiere上で確認"))}
    if path.startswith("/api/jobs/") and path.endswith("/writeback-failed"):
        key = path.split("/")[3]
        return {"ok": True, "job": MANAGER.writeback_failed(key, payload.get("error", "不明なエラー"), payload.get("restored", False))}
    if path.startswith("/api/jobs/") and path.endswith("/request-restore"):
        key = path.split("/")[3]
        return {"ok": True, "job": MANAGER.request_restore(key)}
    if path.startswith("/api/jobs/") and path.endswith("/restore-json"):
        key = path.split("/")[3]
        return {"ok": True, "json": MANAGER.restore_json(key)}
    if path.startswith("/api/jobs/") and path.endswith("/restore-complete"):
        key = path.split("/")[3]
        return {"ok": True, "job": MANAGER.restore_complete(key, payload.get("saved", False))}
    if path.startswith("/api/jobs/") and path.endswith("/restore-failed"):
        key = path.split("/")[3]
        return {"ok": True, "job": MANAGER.restore_failed(key, payload.get("error", "不明なエラー"))}
    raise ValueError(f"未対応の連携要求です: {path}")


class FileBridge:
    def __init__(self, root: Path = IPC_ROOT) -> None:
        self.root = root
        self.inbox = root / "inbox"
        self.outbox = root / "outbox"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.thread: threading.Thread | None = None

    def process_once(self) -> int:
        processed = 0
        for request_path in sorted(self.inbox.glob("*.json")):
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            request_id = request_path.stem
            try:
                response = dispatch_request(str(request.get("path") or ""), request.get("payload"))
            except Exception as error:
                response = {"ok": False, "error": f"{type(error).__name__}: {error}"}
                write_diagnostic_log(
                    {
                        "source": "file-bridge",
                        "context": str(request.get("path") or "unknown"),
                        "message": response["error"],
                        "stack": traceback.format_exc(),
                    },
                    SERVER_LOG_PATH,
                )
            if not request.get("fire_and_forget"):
                output_path = self.outbox / f"{request_id}.json"
                temporary = self.outbox / f"{request_id}.tmp"
                temporary.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
                temporary.replace(output_path)
            try:
                request_path.unlink()
            except OSError:
                pass
            processed += 1
        return processed

    def run(self) -> None:
        while True:
            self.process_once()
            time.sleep(0.2)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.run, daemon=True, name="uxp-file-bridge")
        self.thread.start()


class BridgeHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            return self._json(dispatch_request(path))
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self._payload()
            return self._json(dispatch_request(path, payload))
        except Exception as error:
            self._json({"ok": False, "error": str(error)}, status=HTTPStatus.BAD_REQUEST)

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, value: dict, status: HTTPStatus = HTTPStatus.OK):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def main() -> None:
    file_bridge = FileBridge()
    file_bridge.start()
    server = ThreadingHTTPServer(("127.0.0.1", 8878), BridgeHandler)
    print(f"Premiere UXP連携: ローカルJSON {IPC_ROOT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
