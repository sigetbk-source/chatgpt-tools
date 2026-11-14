from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[2] / "ナレーションPremiereハブ_作業" / "runtime"
sys.path.insert(0, str(RUNTIME))
SPEC = importlib.util.spec_from_file_location("narration_voice_workflow_under_test", RUNTIME / "voice_workflow.py")
assert SPEC and SPEC.loader
voice_workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(voice_workflow)
EXTRACT_SPEC = importlib.util.spec_from_file_location("narration_extract_under_test", RUNTIME / "extract_narration.py")
assert EXTRACT_SPEC and EXTRACT_SPEC.loader
extract_narration = importlib.util.module_from_spec(EXTRACT_SPEC)
EXTRACT_SPEC.loader.exec_module(extract_narration)


class NarrationSpeechRuleTests(unittest.TestCase):
    def test_extraction_keeps_line_breaks_only_for_speech_source(self):
        records = extract_narration.narration_block_records([
            "N 一行目",
            "二行目",
            "",
        ])

        self.assertEqual(records, [{"text": "一行目 二行目", "speechSource": "一行目\n二行目"}])
        self.assertEqual(extract_narration.narration_blocks(["N 一行目", "二行目", ""]), ["一行目 二行目"])

    def test_flattened_pdf_text_is_detected(self):
        flattened = [
            "見出し\n Ｎ 一つ目 ＯＮ 会話 Ｎ 二つ目 Ｎ 三つ目\nページ番号",
        ]

        self.assertTrue(extract_narration._pdf_text_is_flattened(flattened))
        self.assertFalse(extract_narration._pdf_text_is_flattened(["Ｎ 一つ目\n\nＮ 二つ目\n"]))

    def test_positioned_pdf_lines_keep_right_script_column_and_visual_blanks(self):
        fragments = [
            (40.0, 700.0, 12.0, "＊左欄"),
            (155.0, 700.0, 12.0, "Ｎ"),
            (179.0, 700.0, 12.0, "一行目"),
            (179.0, 682.0, 12.0, "二行目"),
            (0.0, 670.0, 12.0, "壊れた手書き文字"),
            (155.0, 640.0, 12.0, "Ｎ"),
            (179.0, 640.0, 12.0, "次の本文"),
        ]

        lines = extract_narration._rebuild_positioned_page_lines(
            fragments,
            page_width=595.0,
            content_start_ratio=0.245,
        )

        self.assertEqual(lines[:4], ["Ｎ 一行目", "二行目", "", "Ｎ 次の本文"])

    def test_dialogue_and_direction_end_narration_when_pdf_blank_line_is_lost(self):
        records = extract_narration.narration_block_records([
            "Ｎ ナレーションだけ",
            "キャスター「これは会話」",
            "Ｎ 次のナレーション",
            "＊映像指示",
        ])

        self.assertEqual(
            records,
            [
                {"text": "ナレーションだけ", "speechSource": "ナレーションだけ"},
                {"text": "次のナレーション", "speechSource": "次のナレーション"},
            ],
        )

    def test_at_placeholders_become_one_maru_per_consecutive_group(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_dictionary = Path(directory) / "pronunciations.csv"
            with patch.object(voice_workflow, "PRONUNCIATIONS_CSV", missing_dictionary):
                speech, note = voice_workflow.normalize_speech("そして１９＠＠年＠月＠＠日")

        self.assertEqual(speech, "そして１９マル年マル月マル日")
        self.assertIn("伏字記号をマルに変換", note)

    def test_full_and_half_width_at_runs_each_become_one_maru(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_dictionary = Path(directory) / "pronunciations.csv"
            with patch.object(voice_workflow, "PRONUNCIATIONS_CSV", missing_dictionary):
                speech, _ = voice_workflow.normalize_speech("＠＠月@@日")

        self.assertEqual(speech, "マル月マル日")

    def test_circle_placeholders_become_one_maru_per_consecutive_group(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_dictionary = Path(directory) / "pronunciations.csv"
            with patch.object(voice_workflow, "PRONUNCIATIONS_CSV", missing_dictionary):
                speech, _ = voice_workflow.normalize_speech("●●人、○○月、〇◯◎◉⚫️⚪日")

        self.assertEqual(speech, "マル人、マル月、マル日")

    def test_line_break_adds_period_when_previous_line_has_no_punctuation(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_dictionary = Path(directory) / "pronunciations.csv"
            with patch.object(voice_workflow, "PRONUNCIATIONS_CSV", missing_dictionary):
                speech, note = voice_workflow.normalize_speech("一行目\n二行目")

        self.assertEqual(speech, "一行目。二行目")
        self.assertIn("改行末に句点を追加", note)

    def test_line_break_after_comma_keeps_comma_without_period(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_dictionary = Path(directory) / "pronunciations.csv"
            with patch.object(voice_workflow, "PRONUNCIATIONS_CSV", missing_dictionary):
                speech, note = voice_workflow.normalize_speech("一行目、\n二行目")

        self.assertEqual(speech, "一行目、二行目")
        self.assertNotIn("改行末に句点を追加", note)


if __name__ == "__main__":
    unittest.main()
