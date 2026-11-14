from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1] / "deployment" / "local-runtime"
SPEC = importlib.util.spec_from_file_location("narration_jobs_under_test", RUNTIME / "narration_jobs.py")
assert SPEC and SPEC.loader
narration_jobs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(narration_jobs)


class NarrationJobsTests(unittest.TestCase):
    def test_list_jobs_skips_inaccessible_entry_and_keeps_valid_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = root / "valid-job"
            blocked = root / "blocked-job"
            valid.mkdir()
            blocked.mkdir()
            (valid / "status.json").write_text(json.dumps({
                "jobId": "valid", "state": "applied", "updatedAt": "2026-08-07T12:00:00+09:00",
            }), encoding="utf-8")
            (valid / "context.json").write_text(json.dumps({
                "caseFolder": "/case/サンプル案件", "mode": "audio",
            }, ensure_ascii=False), encoding="utf-8")
            index = root / "index.json"
            index.write_text(json.dumps({"jobs": [
                {"id": "blocked", "path": str(blocked)},
                {"id": "valid", "path": str(valid)},
            ]}), encoding="utf-8")

            real_summary = narration_jobs.summary

            def guarded_summary(path: Path) -> dict:
                if path == blocked:
                    raise PermissionError("cloud item unavailable")
                return real_summary(path)

            with patch.object(narration_jobs, "INDEX_PATH", index), patch.object(
                narration_jobs, "summary", side_effect=guarded_summary
            ):
                jobs = narration_jobs.list_jobs()

            self.assertEqual([job["id"] for job in jobs], ["valid"])
            self.assertEqual(jobs[0]["caseName"], "サンプル案件")


if __name__ == "__main__":
    unittest.main()
