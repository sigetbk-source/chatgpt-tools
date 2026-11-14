from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[2] / "ナレーションPremiereハブ_作業" / "runtime"
sys.path.insert(0, str(RUNTIME))
SPEC = importlib.util.spec_from_file_location("narration_diff_update_under_test", RUNTIME / "diff_update.py")
assert SPEC and SPEC.loader
diff_update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diff_update)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def make_project(root: Path) -> Path:
    project = root / "case" / "narration_timeline"
    project.mkdir(parents=True)
    items = [
        {"id": "N001", "text": "最初です。", "audioFile": "voice_audio/N001.wav", "durationSeconds": 1, "startSeconds": 0, "endSeconds": 1},
        {"id": "N002", "text": "二番目です。", "audioFile": "voice_audio/N002.wav", "durationSeconds": 1, "startSeconds": 1, "endSeconds": 2},
        {"id": "N003", "text": "三番目です。", "audioFile": "voice_audio/N003.wav", "durationSeconds": 1, "startSeconds": 2, "endSeconds": 3},
    ]
    write_json(project / "narration.json", {"schemaVersion": 2, "items": items})
    (project / "narration.csv").write_text("id,startSeconds,endSeconds,durationSeconds,text\n", encoding="utf-8")
    (project / "speech_review.csv").write_text("id,status,captionText,speechText,note\n", encoding="utf-8")
    write_json(project / "voice_state.json", {"items": {}})
    (project / "narration_audio.srt").write_text("old", encoding="utf-8")
    return project


def fake_finalize(project: Path, _changed_ids: list[str]) -> None:
    payload = json.loads((project / "narration.json").read_text(encoding="utf-8"))
    cursor = 0.0
    audio = project / "voice_audio"
    audio.mkdir(exist_ok=True)
    for item in payload["items"]:
        item["startSeconds"] = cursor
        item["durationSeconds"] = 1.0
        item["endSeconds"] = cursor + 1.0
        item["audioFile"] = f"voice_audio/{item['id']}.wav"
        (project / item["audioFile"]).write_bytes(item["id"].encode())
        cursor += 1.0
    write_json(project / "narration.json", payload)
    (project / "narration_audio.srt").write_text("new", encoding="utf-8")


class NarrationDiffUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_apply_selected_changes_preserves_added_order_and_publishes_manifest(self):
        project = make_project(self.root)
        update_id = "test-update"
        update_dir = project / ".premiere-tools" / "narration-updates" / update_id
        update_dir.mkdir(parents=True)
        current = diff_update._current(project)
        changes = [
            {"type": "modified", "id": "N001", "before": "最初です。", "after": "最初を修正。"},
            {"type": "added", "after": "N001", "text": "追加A"},
            {"type": "added", "after": "N001", "text": "追加B"},
            {"type": "deleted", "id": "N002", "before": "二番目です。"},
        ]
        write_json(update_dir / "plan.json", {
            "schemaVersion": 2, "updateId": update_id, "project": str(project),
            "sourceSnapshot": diff_update._snapshot(current), "changes": changes,
        })
        baseline = {
            "caseName": "case",
            "audioItems": [
                {"id": item["id"], "audioPath": f"/old/{item['id']}.wav", "transcriptPath": f"/old/{item['id']}.json"}
                for item in current
            ],
        }
        with patch.object(diff_update, "_run_workflow", side_effect=fake_finalize):
            result = diff_update.apply_plan(project, update_id, [0, 1, 2, 3], baseline_manifest=baseline)

        updated = json.loads((project / "narration.json").read_text(encoding="utf-8"))["items"]
        self.assertEqual([item["id"] for item in updated], ["N001", "N004", "N005", "N003"])
        self.assertEqual([item["text"] for item in updated], ["最初を修正。", "追加A", "追加B", "三番目です。"])
        self.assertEqual(result["generatedIds"], ["N001", "N004", "N005"])
        manifest = result["manifest"]
        self.assertEqual([item["id"] for item in manifest["audioItems"]], ["N001", "N004", "N005", "N003"])
        self.assertEqual(manifest["audioItems"][-1]["audioPath"], "/old/N003.wav")
        self.assertEqual([operation.get("id") for operation in manifest["operations"]], ["N001", "N004", "N005", "N002"])
        self.assertTrue(Path(manifest["manifestPath"]).is_file())
        self.assertTrue((update_dir / "backup" / "narration.json").is_file())

    def test_apply_rejects_stale_plan_without_changing_project(self):
        project = make_project(self.root)
        update_id = "stale"
        update_dir = project / ".premiere-tools" / "narration-updates" / update_id
        update_dir.mkdir(parents=True)
        before = (project / "narration.json").read_text(encoding="utf-8")
        write_json(update_dir / "plan.json", {
            "schemaVersion": 2, "updateId": update_id, "project": str(project),
            "sourceSnapshot": "not-current", "changes": [{"type": "deleted", "id": "N001"}],
        })
        with self.assertRaisesRegex(ValueError, "比較後"):
            diff_update.apply_plan(project, update_id, [0])
        self.assertEqual((project / "narration.json").read_text(encoding="utf-8"), before)
        self.assertFalse((update_dir / "backup").exists())

    def test_refresh_reviewed_update_republishes_edited_audio_and_preserves_binding(self):
        project = make_project(self.root)
        update_id = "reviewed"
        update_dir = project / ".premiere-tools" / "narration-updates" / update_id
        update_dir.mkdir(parents=True)
        current = diff_update._current(project)
        write_json(update_dir / "plan.json", {
            "schemaVersion": 2, "updateId": update_id, "project": str(project),
            "sourceSnapshot": diff_update._snapshot(current),
            "changes": [{"type": "modified", "id": "N001", "before": "最初です。", "after": "最初を修正。"}],
        })
        baseline = {
            "caseName": "case",
            "audioItems": [
                {"id": item["id"], "audioPath": f"/old/{item['id']}.wav", "transcriptPath": f"/old/{item['id']}.json"}
                for item in current
            ],
        }
        with patch.object(diff_update, "_run_workflow", side_effect=fake_finalize):
            first = diff_update.apply_plan(project, update_id, [0], baseline_manifest=baseline)

        stored = json.loads(Path(first["manifest"]["manifestPath"]).read_text(encoding="utf-8"))
        stored["premiereBinding"] = {"audioTrackIndex": 1, "items": [{"id": "N001"}]}
        write_json(Path(first["manifest"]["manifestPath"]), stored)
        narration = json.loads((project / "narration.json").read_text(encoding="utf-8"))
        narration["items"][0]["text"] = "ブラウザで再修正。"
        write_json(project / "narration.json", narration)
        (project / "voice_audio" / "N001.wav").write_bytes(b"reviewed-audio")
        previous_audio = first["manifest"]["audioItems"][0]["audioPath"]

        refreshed = diff_update.refresh_reviewed_update(project, update_id)

        refreshed_by_id = {item["id"]: item for item in refreshed["audioItems"]}
        self.assertNotEqual(refreshed_by_id["N001"]["audioPath"], previous_audio)
        self.assertEqual(Path(refreshed_by_id["N001"]["audioPath"]).read_bytes(), b"reviewed-audio")
        self.assertEqual(refreshed_by_id["N003"]["audioPath"], "/old/N003.wav")
        self.assertEqual(refreshed_by_id["N001"]["captionText"], "ブラウザで再修正。")
        transcript = json.loads(Path(refreshed_by_id["N001"]["transcriptPath"]).read_text(encoding="utf-8"))
        self.assertEqual(transcript["segments"][0]["words"][0]["text"], "ブラウザで再修正。")
        self.assertEqual(refreshed["premiereBinding"]["audioTrackIndex"], 1)
        applied = json.loads((update_dir / "applied.json").read_text(encoding="utf-8"))
        self.assertTrue(applied.get("reviewedAt"))

    def test_failure_restores_all_management_files(self):
        project = make_project(self.root)
        update_id = "failure"
        update_dir = project / ".premiere-tools" / "narration-updates" / update_id
        update_dir.mkdir(parents=True)
        current = diff_update._current(project)
        write_json(update_dir / "plan.json", {
            "schemaVersion": 2, "updateId": update_id, "project": str(project),
            "sourceSnapshot": diff_update._snapshot(current),
            "changes": [{"type": "modified", "id": "N001", "after": "失敗する修正"}],
        })
        before = {name: (project / name).read_bytes() for name in diff_update.MANAGED_FILES}

        def fail(_project: Path, _ids: list[str]) -> None:
            (project / "narration_audio.srt").write_text("broken", encoding="utf-8")
            raise RuntimeError("generation failed")

        with patch.object(diff_update, "_run_workflow", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "generation failed"):
                diff_update.apply_plan(project, update_id, [0])
        self.assertEqual({name: (project / name).read_bytes() for name in diff_update.MANAGED_FILES}, before)
        self.assertFalse((update_dir / "applied.json").exists())


if __name__ == "__main__":
    unittest.main()
