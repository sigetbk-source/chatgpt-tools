from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from speaker_repair.narration_sync import synchronize_narration_srt


class NarrationSrtSyncTests(unittest.TestCase):
    def test_srt_uses_actual_premiere_times_and_updates_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_srt = root / "old.srt"
            old_srt.write_text("old", encoding="utf-8")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps({
                "revision": "r1",
                "srtPath": str(old_srt),
                "audioItems": [
                    {"id": "N001", "captionText": "最初。"},
                    {"id": "N002", "captionText": "次。"},
                ],
            }, ensure_ascii=False), encoding="utf-8")

            result = synchronize_narration_srt(manifest_path, [
                {"id": "N001", "startSeconds": 1.0, "endSeconds": 2.001},
                {"id": "N002", "startSeconds": 2.001, "endSeconds": 3.5},
            ])

            content = Path(result["srtPath"]).read_text(encoding="utf-8")
            self.assertIn("00:00:01,000 --> 00:00:02,001", content)
            self.assertIn("00:00:02,001 --> 00:00:03,500", content)
            self.assertIn("N　次。", content)
            refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(refreshed["srtPath"], result["srtPath"])
            self.assertEqual(refreshed["audioItems"][1]["startSeconds"], 2.001)

    def test_rejects_id_mismatch_without_replacing_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = root / "old.srt"
            srt.write_text("old", encoding="utf-8")
            manifest_path = root / "manifest.json"
            original = json.dumps({
                "srtPath": str(srt),
                "audioItems": [{"id": "N001", "captionText": "本文"}],
            }, ensure_ascii=False)
            manifest_path.write_text(original, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ID"):
                synchronize_narration_srt(manifest_path, [
                    {"id": "N999", "startSeconds": 0, "endSeconds": 1},
                ])
            self.assertEqual(manifest_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
