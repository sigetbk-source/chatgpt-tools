from __future__ import annotations

import json
import mimetypes
import argparse
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .corrections import (
    build_change_highlights,
    canonicalize_terms,
    export_corrected_csv,
    restore_original_selection,
)
from .project import Project
from .speaker_registry import (
    add_speaker_names,
    is_placeholder_speaker,
    read_speaker_registry,
    replace_speaker_name,
)
from .segment_edits import (
    history_status,
    load_edit_state,
    merge_with_next,
    record_history,
    redo,
    split_segment,
    undo,
)
from .uxp_automation import WritebackError, _bridge_request, run_browser_writeback


STATIC_DIR = Path(__file__).parent / "transcript_static"
VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v", ".mxf"}


def is_video_source(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_SUFFIXES


def review_media_path(project: Project) -> Path:
    return project.review_video_path if project.review_video_path.exists() else project.audio


def update_speaker_decisions(candidates: dict, decisions: dict, payload: dict) -> tuple[dict, list[int]]:
    segment_index = int(payload["segment_index"])
    speaker_name = str(payload.get("speaker_name") or "").strip()
    if not speaker_name:
        raise ValueError("話者名を入力してください")
    item = next(
        (candidate for candidate in candidates.get("items", []) if candidate["segment_index"] == segment_index),
        None,
    )
    if item is None:
        raise ValueError("区間が見つかりません")
    original_name = str(payload.get("original_speaker_name") or item["speaker_name"])
    scope = "bulk" if payload.get("scope") == "bulk" else "segment"
    targets = (
        [candidate for candidate in candidates["items"] if candidate["speaker_name"] == original_name]
        if scope == "bulk"
        else [item]
    )
    changed = []
    for target in targets:
        key = str(target["segment_index"])
        decision = dict(decisions.get(key, {}))
        decision.update({
            "segment_index": target["segment_index"],
            "speaker_name": speaker_name,
            "speaker_original": original_name,
            "speaker_scope": scope,
            "updated_at": payload.get("updated_at"),
        })
        decisions[key] = decision
        changed.append(target["segment_index"])
    return decisions, changed


def manage_speaker_decisions(
    candidates: dict, decisions: dict, payload: dict
) -> tuple[dict, list[int], str, str]:
    """Rename/merge one effective speaker, or delete an unused registry entry."""
    old_name = str(payload.get("old_name") or "").strip()
    new_name = str(payload.get("new_name") or "").strip()
    action = str(payload.get("action") or "replace")
    if not old_name:
        raise ValueError("管理する話者名を選んでください")
    if is_placeholder_speaker(old_name):
        raise ValueError("初期話者名は削除・統合できません")
    if new_name == old_name:
        raise ValueError("変更前と変更後が同じ名前です")
    if action not in {"replace", "delete"}:
        raise ValueError("話者管理の操作が不正です")
    if action == "replace" and not new_name:
        raise ValueError("変更・統合先の話者名を指定してください")

    updated = {key: dict(value) for key, value in decisions.items()}
    targets = []
    for item in candidates.get("items", []):
        decision = updated.get(str(item["segment_index"]), {})
        effective_name = str(decision.get("speaker_name") or item.get("speaker_name") or "")
        if effective_name == old_name:
            targets.append(item)
    if targets and not new_name:
        raise ValueError("使用中の話者名です。置換先を選んでから削除してください")

    changed = []
    for target in targets:
        key = str(target["segment_index"])
        decision = dict(updated.get(key, {}))
        decision.update({
            "segment_index": target["segment_index"],
            "speaker_name": new_name,
            "speaker_original": old_name,
            "speaker_scope": "managed",
            "updated_at": payload.get("updated_at"),
        })
        updated[key] = decision
        changed.append(target["segment_index"])
    return updated, changed, old_name, new_name


class TranscriptHandler(BaseHTTPRequestHandler):
    project: Project

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            candidates = self._candidates()
            return self._send_json({"ok": True, "project": self.project.project_name, "items": len(candidates["items"])})
        if path == "/api/project":
            candidates = self._candidates()
            edit_state = load_edit_state(self.project, self._base_candidates())
            references = json.loads(self.project.references_path.read_text(encoding="utf-8"))
            decisions = self._decisions()
            speaker_options = add_speaker_names(
                self.project.speaker_registry_path,
                [
                    *(item.get("speaker_name") for item in candidates.get("items", [])),
                    *(item.get("speaker_name") for item in decisions.values()),
                ],
            )
            return self._send_json({
                "job_id": self.project.config_path.parent.name,
                "project_name": self.project.project_name,
                "source_media_name": self.project.audio.name,
                "has_video": is_video_source(self.project.audio),
                "candidates": candidates,
                "decisions": decisions,
                "speaker_options": speaker_options,
                "reference_terms": references["terms"],
                "edit_history": history_status(edit_state),
            })
        if path == "/audio.wav":
            return self._send_file(self.project.wav_path, allow_range=True)
        if path == "/media":
            return self._send_file(review_media_path(self.project), allow_range=True)
        target = STATIC_DIR / ("index.html" if path == "/" else path.lstrip("/"))
        if target.is_file() and STATIC_DIR in target.resolve().parents:
            return self._send_file(target, no_cache=True)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if path == "/api/decision":
            decisions = self._decisions()
            if payload.get("status") == "approved" and isinstance(payload.get("text"), str):
                payload["text"], _ = canonicalize_terms(
                    payload["text"], self.project.glossary_aliases
                )
            key = str(payload["segment_index"])
            decisions[key] = {**decisions.get(key, {}), **payload}
            self.project.correction_decisions_path.write_text(
                json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.project.writeback_ready_path.unlink(missing_ok=True)
            return self._send_json({"ok": True, "decision": decisions[key]})
        if path == "/api/speaker":
            base_candidates = self._base_candidates()
            edit_state = load_edit_state(self.project, base_candidates)
            previous_decisions = self._decisions()
            record_history(self.project, edit_state, previous_decisions)
            decisions, changed = update_speaker_decisions(
                self._candidates(), previous_decisions, payload
            )
            self.project.correction_decisions_path.write_text(
                json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.project.writeback_ready_path.unlink(missing_ok=True)
            speaker_options = add_speaker_names(
                self.project.speaker_registry_path, [payload.get("speaker_name")]
            )
            return self._send_json({
                "ok": True,
                "decisions": decisions,
                "changed": changed,
                "speaker_options": speaker_options,
            })
        if path == "/api/speakers/manage":
            base_candidates = self._base_candidates()
            edit_state = load_edit_state(self.project, base_candidates)
            previous_decisions = self._decisions()
            try:
                decisions, changed, old_name, new_name = manage_speaker_decisions(
                    self._candidates(), previous_decisions, payload
                )
            except (KeyError, TypeError, ValueError) as error:
                return self._send_json(
                    {"ok": False, "error": str(error)}, HTTPStatus.BAD_REQUEST
                )
            record_history(
                self.project,
                edit_state,
                previous_decisions,
                include_speaker_registry=True,
            )
            self._write_decisions(decisions)
            speaker_options = replace_speaker_name(
                self.project.speaker_registry_path, old_name, new_name
            )
            self.project.writeback_ready_path.unlink(missing_ok=True)
            return self._send_json({
                "ok": True,
                "decisions": decisions,
                "changed": changed,
                "speaker_options": speaker_options,
                "edit_history": history_status(edit_state),
            })
        if path == "/api/segments/split":
            try:
                state, decisions, new_indices = split_segment(
                    self.project,
                    self._base_candidates(),
                    self._decisions(),
                    int(payload["segment_index"]),
                    int(payload["caret"]),
                    str(payload.get("current_text", "")),
                )
            except (KeyError, TypeError, ValueError) as error:
                return self._send_json({"ok": False, "error": str(error)}, HTTPStatus.BAD_REQUEST)
            self._write_decisions(decisions)
            self.project.writeback_ready_path.unlink(missing_ok=True)
            return self._send_json({"ok": True, "new_segment_indices": new_indices, "edit_history": history_status(state)})
        if path == "/api/segments/merge-next":
            try:
                state, decisions, merged_index = merge_with_next(
                    self.project,
                    self._base_candidates(),
                    self._decisions(),
                    int(payload["segment_index"]),
                    str(payload.get("current_text", "")),
                    str(payload.get("next_text", "")),
                )
            except (KeyError, TypeError, ValueError) as error:
                return self._send_json({"ok": False, "error": str(error)}, HTTPStatus.BAD_REQUEST)
            self._write_decisions(decisions)
            self.project.writeback_ready_path.unlink(missing_ok=True)
            return self._send_json({"ok": True, "merged_segment_index": merged_index, "edit_history": history_status(state)})
        if path in {"/api/segments/undo", "/api/segments/redo"}:
            try:
                operation = undo if path.endswith("/undo") else redo
                state, decisions = operation(
                    self.project, self._base_candidates(), self._decisions()
                )
            except ValueError as error:
                return self._send_json({"ok": False, "error": str(error)}, HTTPStatus.BAD_REQUEST)
            self._write_decisions(decisions)
            self.project.writeback_ready_path.unlink(missing_ok=True)
            return self._send_json({"ok": True, "edit_history": history_status(state)})
        if path == "/api/export":
            return self._send_json({"ok": True, **export_corrected_csv(self.project)})
        if path == "/api/export-and-writeback":
            try:
                exported = export_corrected_csv(self.project)
                if not exported.get("json"):
                    raise WritebackError(exported.get("json_warning", "修正済みJSONを作れませんでした"))
                job_id = self.project.config_path.parent.name
                _bridge_request(f"/api/jobs/{job_id}/request-writeback")
                result = run_browser_writeback(job_id)
                status = HTTPStatus.OK if result.get("ok") else HTTPStatus.CONFLICT
                return self._send_json({"ok": bool(result.get("ok")), "export": exported, **result}, status)
            except WritebackError as error:
                return self._send_json(
                    {"ok": False, "code": error.code, "error": str(error)},
                    HTTPStatus.CONFLICT,
                )
        if path == "/api/restore-selection":
            item = next(
                (
                    candidate
                    for candidate in self._candidates()["items"]
                    if candidate["segment_index"] == int(payload["segment_index"])
                ),
                None,
            )
            if item is None:
                return self._send_json({"ok": False, "error": "区間が見つかりません"})
            result = restore_original_selection(
                (
                    item["whisper_text"]
                    if payload.get("selection_source") == "whisper"
                    else item["original_text"]
                ),
                payload["current_text"],
                payload["selection_start"],
                payload["selection_end"],
            )
            result["text"], _ = canonicalize_terms(
                result["text"], self.project.glossary_aliases
            )
            return self._send_json({"ok": True, **result})
        self.send_error(HTTPStatus.NOT_FOUND)

    def _base_candidates(self):
        return json.loads(self.project.correction_candidates_path.read_text(encoding="utf-8"))

    def _candidates(self):
        base = self._base_candidates()
        state = load_edit_state(self.project, base)
        candidates = {**base, "items": state["items"]}
        for item in candidates.get("items", []):
            reference_evidence_terms = [
                *item.get("exact_reference_terms", []),
                *(
                    candidate.get("term", "")
                    for candidate in item.get("possible_reference_terms", [])
                    if float(candidate.get("score", 0)) >= 0.95
                ),
            ]
            item["change_highlights"] = build_change_highlights(
                item.get("original_text", ""),
                item.get("whisper_text", ""),
                item.get("suggested_text", ""),
                item.get("applied_reference_terms", []),
                reference_evidence_terms,
            )
        return candidates

    def _write_decisions(self, decisions):
        self.project.correction_decisions_path.write_text(
            json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _decisions(self):
        if not self.project.correction_decisions_path.exists():
            return {}
        return json.loads(self.project.correction_decisions_path.read_text(encoding="utf-8"))

    def _send_json(self, value, status=HTTPStatus.OK):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, allow_range: bool = False, no_cache: bool = False):
        size = path.stat().st_size
        start, end = 0, size - 1
        status = HTTPStatus.OK
        if allow_range and self.headers.get("Range", "").startswith("bytes="):
            spec = self.headers["Range"].split("=", 1)[1].split(",", 1)[0]
            left, right = spec.split("-", 1)
            start = int(left) if left else 0
            end = min(int(right) if right else end, size - 1)
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if no_cache:
            self.send_header("Cache-Control", "no-store")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    project = Project.load(args.project)
    TranscriptHandler.project = project
    server = ThreadingHTTPServer(("127.0.0.1", args.port), TranscriptHandler)
    print(f"文字起こし修正画面: http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
