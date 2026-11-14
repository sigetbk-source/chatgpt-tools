from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from speaker_repair import uxp_bridge


class FakeNarrationJobs:
    @staticmethod
    def read_json(path: Path) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @staticmethod
    def write_json(path: Path, value: dict) -> None:
        Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def update(cls, job_path: Path, state: str, message: str, **extra: object) -> dict:
        status_path = Path(job_path) / "status.json"
        status = cls.read_json(status_path)
        status.update({"state": state, "message": message, **extra})
        cls.write_json(status_path, status)
        return status


class NarrationAudioRevisionBridgeTests(unittest.TestCase):
    def make_job(self, root: Path, state: str = "applied") -> Path:
        job = root / "job-1"
        (job / "result").mkdir(parents=True)
        project = root / "case" / "narration_timeline"
        project.mkdir(parents=True)
        (project / "narration.json").write_text("{}", encoding="utf-8")
        binding = {"audioTrack": 1, "items": [{"id": "N001", "mediaPath": "/old/N001.wav", "order": 0}]}
        (job / "status.json").write_text(json.dumps({"state": state, "premiereBinding": binding}), encoding="utf-8")
        (job / "result" / "premiere_manifest.json").write_text(json.dumps({
            "mode": "audio",
            "projectPath": str(project),
            "audioItems": [{"id": "N001", "audioPath": "/old/N001.wav"}],
        }), encoding="utf-8")
        return job

    def test_open_audio_revision_preserves_binding_and_opens_browser_server(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            with (
                patch.object(uxp_bridge, "narration_job", return_value=(FakeNarrationJobs, job)),
                patch.object(uxp_bridge, "open_narration_audio_revision", return_value={"opened": True}) as opener,
            ):
                result = uxp_bridge.dispatch_request("/api/narration/jobs/job-1/audio-revision-open")
            self.assertTrue(result["ok"])
            opener.assert_called_once()
            manifest = FakeNarrationJobs.read_json(job / "result" / "premiere_manifest.json")
            self.assertEqual(manifest["premiereBinding"]["audioTrack"], 1)

    def test_audio_update_manifest_requires_browser_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory), state="audio_editing")
            with patch.object(uxp_bridge, "narration_job", return_value=(FakeNarrationJobs, job)):
                with self.assertRaisesRegex(ValueError, "承認"):
                    uxp_bridge.dispatch_request("/api/narration/jobs/job-1/audio-update-manifest")

    def test_audio_update_complete_returns_job_to_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory), state="audio_update_ready")
            new_binding = {"audioTrack": 1, "items": [{"id": "N001", "mediaPath": "/new/N001.wav", "order": 0}]}
            with patch.object(uxp_bridge, "narration_job", return_value=(FakeNarrationJobs, job)):
                result = uxp_bridge.dispatch_request(
                    "/api/narration/jobs/job-1/audio-update-complete",
                    {"premiereBinding": new_binding},
                )
            self.assertEqual(result["job"]["state"], "applied")
            self.assertEqual(result["job"]["captionState"], "requested")
            manifest = FakeNarrationJobs.read_json(job / "result" / "premiere_manifest.json")
            self.assertEqual(manifest["premiereBinding"]["items"][0]["mediaPath"], "/new/N001.wav")

    def test_sync_srt_keeps_caption_replacement_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory), state="applied")
            synchronized = {
                "srtPath": "/new/synced.srt",
                "manifest": {"srtPath": "/new/synced.srt", "audioItems": []},
            }
            with (
                patch.object(uxp_bridge, "narration_job", return_value=(FakeNarrationJobs, job)),
                patch.object(uxp_bridge, "synchronize_narration_srt", return_value=synchronized),
            ):
                result = uxp_bridge.dispatch_request(
                    "/api/narration/jobs/job-1/sync-srt",
                    {"timings": [{"id": "N001", "startSeconds": 0, "endSeconds": 1}]},
                )
            self.assertEqual(result["srtPath"], "/new/synced.srt")
            status = FakeNarrationJobs.read_json(job / "status.json")
            self.assertEqual(status["captionState"], "requested")
            self.assertIn("SRTを貼り直し", status["message"])


if __name__ == "__main__":
    unittest.main()
