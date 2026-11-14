import json
import tempfile
import unittest
from pathlib import Path

from speaker_repair.project import DEFAULT_MODEL_CACHE_DIR, Project


class ProjectModelCacheTests(unittest.TestCase):
    def _write_config(self, root: Path, **overrides) -> Path:
        config = {
            "project_name": "model cache test",
            "source_dir": str(root),
            "transcript_json": "transcript.json",
            "source_media": "media.wav",
            "analysis_dir": str(root / "analysis"),
            **overrides,
        }
        config_path = root / "project.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path

    def test_default_model_cache_is_shared_across_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Project.load(self._write_config(Path(directory)))
            self.assertEqual(project.model_cache_dir, DEFAULT_MODEL_CACHE_DIR)

    def test_relative_model_cache_override_is_resolved_from_repository_root(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Project.load(
                self._write_config(Path(directory), model_cache_dir="work/custom-cache")
            )
            self.assertEqual(
                project.model_cache_dir,
                DEFAULT_MODEL_CACHE_DIR.parent / "custom-cache",
            )


if __name__ == "__main__":
    unittest.main()
