import tempfile
import unittest
from pathlib import Path

from speaker_repair.speaker_registry import (
    add_speaker_names,
    is_placeholder_speaker,
    read_speaker_registry,
    replace_speaker_name,
    write_speaker_registry,
)


class SpeakerRegistryTests(unittest.TestCase):
    def test_placeholder_labels_are_not_shared_across_assets(self):
        self.assertTrue(is_placeholder_speaker("話者＠"))
        self.assertTrue(is_placeholder_speaker("不明"))
        self.assertTrue(is_placeholder_speaker("話者 4"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project-speakers.json"
            add_speaker_names(path, ["話者＠", "不明", "野嶋咲子"])
            self.assertEqual(read_speaker_registry(path), ["野嶋咲子"])

    def test_confirmed_name_becomes_project_wide_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project-speakers.json"
            add_speaker_names(path, ["野嶋咲子"])
            add_speaker_names(path, ["市橋雄大", "野嶋咲子"])
            self.assertEqual(read_speaker_registry(path), ["野嶋咲子", "市橋雄大"])

    def test_registry_can_rename_merge_and_delete_names(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project-speakers.json"
            write_speaker_registry(path, ["名取菜保子", "名取菜穂子", "未使用"])
            replace_speaker_name(path, "名取菜保子", "名取菜穂子")
            self.assertEqual(read_speaker_registry(path), ["名取菜穂子", "未使用"])
            replace_speaker_name(path, "未使用")
            self.assertEqual(read_speaker_registry(path), ["名取菜穂子"])


if __name__ == "__main__":
    unittest.main()
