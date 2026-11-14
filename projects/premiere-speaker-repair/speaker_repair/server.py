from __future__ import annotations

import json
import mimetypes
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .project import Project


STATIC_DIR = Path(__file__).parent / "static"


class ReviewHandler(BaseHTTPRequestHandler):
    project: Project

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._send_health()
        if path == "/api/project":
            return self._send_project()
        if path == "/api/decisions":
            return self._send_json(self._read_decisions())
        if path == "/audio.mp3":
            return self._send_file(self.project.audio, allow_range=True)
        target = STATIC_DIR / ("index.html" if path == "/" else path.lstrip("/"))
        if target.is_file() and STATIC_DIR in target.resolve().parents:
            return self._send_file(target)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {"/api/decision", "/api/bulk-decision"}:
            return self.send_error(HTTPStatus.NOT_FOUND)
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        decisions = self._read_decisions()
        if path == "/api/bulk-decision":
            analysis = json.loads(self.project.results_path.read_text(encoding="utf-8"))
            selected = select_bulk_suggestions(
                analysis,
                speaker_id=payload["speaker_id"],
                candidate=payload["candidate"],
            )
            for item in selected:
                decisions[str(item["segment_index"])] = {
                    "segment_index": item["segment_index"],
                    "status": "approved",
                    "original_speaker": item["original_speaker"],
                    "candidate": item["candidate"],
                    "source": "bulk-strong-only",
                    "updated_at": payload.get("updated_at"),
                }
            response = {"ok": True, "count": len(selected)}
        else:
            decisions[str(payload["segment_index"])] = payload
            response = {"ok": True, "count": 1}
        self.project.decisions_path.parent.mkdir(parents=True, exist_ok=True)
        self.project.decisions_path.write_text(
            json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._send_json(response)

    def _send_project(self):
        manifest = json.loads(self.project.manifest_path.read_text(encoding="utf-8"))
        analysis = (
            json.loads(self.project.results_path.read_text(encoding="utf-8"))
            if self.project.results_path.exists()
            else {"segment_suggestions": [], "label_diagnostics": []}
        )
        self._send_json({"manifest": manifest, "analysis": analysis, "decisions": self._read_decisions()})

    def _send_health(self):
        manifest = json.loads(self.project.manifest_path.read_text(encoding="utf-8"))
        analysis = json.loads(self.project.results_path.read_text(encoding="utf-8"))
        self._send_json({
            "ok": True,
            "project": self.project.project_name,
            "segments": manifest["segment_count"],
            "analyzed_chunks": len(analysis["chunks"]),
            "known_speakers": analysis["known_speakers"],
        })

    def _read_decisions(self):
        if not self.project.decisions_path.exists():
            return {}
        return json.loads(self.project.decisions_path.read_text(encoding="utf-8"))

    def _send_json(self, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, allow_range: bool = False):
        size = path.stat().st_size
        start, end = 0, size - 1
        status = HTTPStatus.OK
        if allow_range and self.headers.get("Range", "").startswith("bytes="):
            spec = self.headers["Range"].split("=", 1)[1].split(",", 1)[0]
            left, right = spec.split("-", 1)
            start = int(left) if left else 0
            end = int(right) if right else end
            end = min(end, size - 1)
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def log_message(self, format, *args):
        return


def select_bulk_suggestions(analysis, speaker_id: str, candidate: str):
    return [
        item
        for item in analysis.get("segment_suggestions", [])
        if item.get("speaker_id") == speaker_id
        and item.get("candidate") == candidate
        and item.get("confidence") == "strong"
        and not item.get("locked")
        and not item.get("possible_internal_change")
    ]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("使い方: python -m speaker_repair.server projects/example.json")
    project = Project.load(sys.argv[1])
    ReviewHandler.project = project
    server = ThreadingHTTPServer(("127.0.0.1", 8876), ReviewHandler)
    print("確認画面: http://127.0.0.1:8876")
    server.serve_forever()


if __name__ == "__main__":
    main()
