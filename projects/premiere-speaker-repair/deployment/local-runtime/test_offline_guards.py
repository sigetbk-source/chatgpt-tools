"""Offline history must neither hydrate iCloud nor rewrite saved job state."""
import importlib.util
import json
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parents[1]))
from speaker_repair.project import OfflineFileError, read_local_text, Project
from speaker_repair.uxp_bridge import JobManager

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

narration = load("local_narration", BASE / "narration_jobs.py")
migration = load("migration", BASE / "migrate_state.py")

class OfflineTests(unittest.TestCase):
    def test_placeholder_content_never_read(self):
        with patch.object(Path, "stat", return_value=SimpleNamespace(st_flags=0x40000000)), \
             patch.object(Path, "read_text", side_effect=AssertionError("Must not hydrate")):
            with self.assertRaises(OfflineFileError):
                read_local_text(Path("placeholder.json"))

    def test_job_view_is_offline_but_original_status_is_kept(self):
        manager = JobManager.__new__(JobManager)
        manager.lock = threading.RLock()
        manager.jobs = {"id": {"id": "id", "config_path": "/offline/config.json", "status": "ready_to_writeback", "created_at": 1}}
        manager._save = Mock(side_effect=AssertionError("Must not overwrite history"))
        with patch.object(Project, "load", side_effect=OfflineFileError("iCloud未ダウンロード")):
            result = manager.list_jobs()
        self.assertEqual(result[0]["status"], "offline")
        self.assertEqual(manager.jobs["id"]["status"], "ready_to_writeback")
        manager._save.assert_not_called()

    def test_narration_summary_keeps_placeholder_case_visible(self):
        job = Path("/cases/Case/.premiere-tools/jobs/old-job")
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat", return_value=SimpleNamespace(st_flags=0x40000000, st_mode=stat.S_IFREG)), \
             patch.object(narration, "read_json", side_effect=AssertionError("Must not hydrate")):
            summary = narration.summary(job)
        self.assertEqual(summary["state"], "offline")
        self.assertEqual(summary["caseName"], "Case")
        self.assertFalse(summary["resultReady"])

    def test_bad_dictionary_aborts_before_creating_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "old"; stage = root / "new"
            old = source / "work" / "uxp"; (old / "case-glossaries").mkdir(parents=True)
            (old / "jobs.json").write_text("{}")
            (old / "case-glossaries" / "bad.json").write_text("not json")
            with self.assertRaises(json.JSONDecodeError):
                migration.migrate(source, stage)
            self.assertFalse((stage / "app" / "work" / "uxp").exists())

    def test_dataless_dictionary_aborts_before_creating_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "old"; stage = root / "new"
            old = source / "work" / "uxp"; old.mkdir(parents=True)
            (old / "jobs.json").write_text("{}")
            with patch.object(migration, "is_dataless", return_value=True):
                with self.assertRaises(ValueError):
                    migration.migrate(source, stage)
            self.assertFalse((stage / "app" / "work" / "uxp").exists())

    def test_metadata_migration_preserves_paths_and_makes_new_ipc(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "old"; stage = root / "new"
            old = source / "work" / "uxp"; (old / "ipc" / "inbox").mkdir(parents=True)
            payload = {"job": {"config_path": "/old/case/config.json"}}
            (old / "jobs.json").write_text(json.dumps(payload))
            (old / "ipc" / "inbox" / "stale.json").write_text("{}")
            migration.migrate(source, stage)
            new = stage / "app" / "work" / "uxp"
            self.assertEqual(json.loads((new / "jobs.json").read_text()), payload)
            self.assertTrue((new / "ipc" / "inbox").is_dir())
            self.assertFalse(any((new / "ipc" / "inbox").iterdir()))

if __name__ == "__main__":
    unittest.main()
