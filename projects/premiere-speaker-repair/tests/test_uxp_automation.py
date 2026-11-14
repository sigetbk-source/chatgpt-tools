import json
import tempfile
import unittest
from pathlib import Path

from speaker_repair.uxp_automation import _plugin_source, _project_name


class UxpAutomationTests(unittest.TestCase):
    def test_project_name_falls_back_to_job_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "project.json"
            config.write_text(
                json.dumps({"project_name": "書き出し元.prproj｜素材.mov"}),
                encoding="utf-8",
            )
            self.assertEqual(_project_name({"config_path": str(config)}), "書き出し元.prproj")

    def test_generated_command_has_all_three_target_checks(self):
        source = _plugin_source({
            "projectGuid": "project-guid",
            "projectName": "target.prproj",
            "assetId": "asset-id",
            "assetName": "clip.mov",
            "mediaPath": "/media/clip.mov",
            "correctedPath": "/tmp/corrected.json",
            "backupPath": "/tmp/backup.json",
            "resultPath": "/tmp/result.json",
        })
        self.assertIn('code:"project_mismatch"', source)
        self.assertIn('String(item.getId()) === String(spec.assetId)', source)
        self.assertIn('normalizedPath(spec.mediaPath)', source)


if __name__ == "__main__":
    unittest.main()
