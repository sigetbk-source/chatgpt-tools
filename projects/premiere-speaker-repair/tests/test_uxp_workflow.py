import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from speaker_repair.manifest import build_manifest
from speaker_repair.project import Project
from speaker_repair.references import build_person_dictionary, extract_terms
from speaker_repair.corrections import build_structurally_corrected_transcript_json, export_corrected_csv
from speaker_repair.segment_edits import (
    merge_with_next,
    record_history,
    redo,
    split_segment,
    undo,
    validate_structural_items,
)
from speaker_repair.speaker_registry import (
    read_speaker_registry,
    replace_speaker_name,
    write_speaker_registry,
)
from speaker_repair.transcript_server import (
    is_video_source,
    manage_speaker_decisions,
    update_speaker_decisions,
)
from speaker_repair.uxp_bridge import transcript_has_words
from speaker_repair.uxp_bridge import (
    FileBridge,
    discover_reference_documents,
    latest_diagnostic_logs,
    prepare_inputs_parallel,
    wait_for_review_proxy,
    write_diagnostic_log,
)


class UxpWorkflowTests(unittest.TestCase):
    def test_segment_split_merge_resplit_and_history_preserve_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = {
                "language": "ja-jp",
                "segments": [
                    {
                        "speaker": "speaker-a", "start": 0.0, "duration": 3.0,
                        "words": [
                            {"text": "今日は", "start": 0.0, "duration": 1.0},
                            {"text": "良い", "start": 1.0, "duration": 1.0},
                            {"text": "天気。", "start": 2.0, "duration": 1.0},
                        ],
                    },
                    {
                        "speaker": "speaker-b", "start": 3.0, "duration": 1.0,
                        "words": [{"text": "次です。", "start": 3.0, "duration": 1.0}],
                    },
                ],
                "speakers": [
                    {"id": "speaker-a", "name": "話者A"},
                    {"id": "speaker-b", "name": "話者B"},
                ],
            }
            (root / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
            (root / "media.wav").write_bytes(b"")
            config = {
                "project_name": "segment edit test",
                "source_dir": str(root),
                "transcript_json": "transcript.json",
                "source_media": "media.wav",
                "analysis_dir": str(root / "analysis"),
            }
            config_path = root / "project.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            project = Project.load(config_path)
            candidates = {"items": [
                {
                    "segment_index": 0, "speaker_name": "話者A", "speaker_id": "speaker-a",
                    "start_seconds": 0.0, "duration_seconds": 3.0,
                    "premiere_start": "00:00:00.000", "premiere_end": "00:00:03.000",
                    "original_text": "今日は良い天気。", "whisper_text": "今日は良い天気。",
                    "suggested_text": "今日は良い天気。", "change_score": 0.0, "needs_review": False,
                },
                {
                    "segment_index": 1, "speaker_name": "話者B", "speaker_id": "speaker-b",
                    "start_seconds": 3.0, "duration_seconds": 1.0,
                    "premiere_start": "00:00:03.000", "premiere_end": "00:00:04.000",
                    "original_text": "次です。", "whisper_text": "次です。",
                    "suggested_text": "次です。", "change_score": 0.0, "needs_review": False,
                },
            ]}
            decisions = {"0": {"segment_index": 0, "status": "approved", "text": "今日は良い天気。", "speaker_name": "話者A"}}

            state, decisions, split_ids = split_segment(
                project, candidates, decisions, 0, 3, "今日は良い天気。"
            )
            self.assertEqual(len(state["items"]), 3)
            self.assertTrue(all(decisions[str(index)]["status"] == "approved" for index in split_ids))

            state, decisions = undo(project, candidates, decisions)
            self.assertEqual(len(state["items"]), 2)
            state, decisions = redo(project, candidates, decisions)
            self.assertEqual(len(state["items"]), 3)

            state, decisions, merged_id = merge_with_next(
                project, candidates, decisions, split_ids[0], "今日は", "良い天気。"
            )
            self.assertEqual(decisions[str(merged_id)]["speaker_name"], "話者A")
            self.assertEqual(decisions[str(merged_id)]["status"], "approved")
            state, decisions, new_split_ids = split_segment(
                project, candidates, decisions, merged_id, 5, "今日は良い天気。"
            )
            self.assertEqual(len(state["items"]), 3)
            validate_structural_items(transcript, state["items"])
            ordered = sorted(state["items"], key=lambda item: item["start_seconds"])
            texts = [
                decisions.get(str(item["segment_index"]), {}).get("text", item["original_text"])
                for item in ordered
            ]
            speakers = [
                decisions.get(str(item["segment_index"]), {}).get("speaker_name", item["speaker_name"])
                for item in ordered
            ]
            compiled = build_structurally_corrected_transcript_json(transcript, ordered, texts, speakers)
            self.assertEqual(len(compiled["segments"]), 3)
            self.assertEqual(
                [word["start"] for segment in compiled["segments"] for word in segment["words"]],
                [0.0, 1.0, 2.0, 3.0],
            )
            state, decisions, cross_merged_id = merge_with_next(
                project,
                candidates,
                decisions,
                new_split_ids[1],
                decisions[str(new_split_ids[1])]["text"],
                "次です。",
            )
            self.assertEqual(len(state["items"]), 2)
            self.assertEqual(decisions[str(cross_merged_id)]["speaker_name"], "話者A")
            validate_structural_items(transcript, state["items"])
            state, decisions = undo(project, candidates, decisions)
            self.assertEqual(len(state["items"]), 3)
            project.correction_candidates_path.write_text(
                json.dumps(candidates, ensure_ascii=False), encoding="utf-8"
            )
            project.correction_decisions_path.write_text(
                json.dumps(decisions, ensure_ascii=False), encoding="utf-8"
            )
            export_result = export_corrected_csv(project)
            self.assertGreaterEqual(int(export_result["patch_count"]), 1)
            exported = json.loads(Path(export_result["json"]).read_text(encoding="utf-8"))
            self.assertEqual(len(exported["segments"]), 3)
            self.assertEqual(
                [word["start"] for segment in exported["segments"] for word in segment["words"]],
                [0.0, 1.0, 2.0, 3.0],
            )
            write_speaker_registry(project.speaker_registry_path, ["誤字", "正名"])
            record_history(
                project,
                state,
                decisions,
                include_speaker_registry=True,
            )
            replace_speaker_name(project.speaker_registry_path, "誤字", "正名")
            managed_decisions = {
                **decisions,
                "1": {"segment_index": 1, "speaker_name": "正名"},
            }
            state, restored_decisions = undo(project, candidates, managed_decisions)
            self.assertEqual(read_speaker_registry(project.speaker_registry_path), ["誤字", "正名"])
            self.assertEqual(restored_decisions, decisions)
            state, redone_decisions = redo(project, candidates, restored_decisions)
            self.assertEqual(read_speaker_registry(project.speaker_registry_path), ["正名"])
            self.assertEqual(redone_decisions["1"]["speaker_name"], "正名")

    def test_ordinary_quoted_dialogue_is_not_a_dictionary_term(self):
        terms = extract_terms(
            '井上「こんにちは」 店主「いらっしゃいませ」 '
            '商品「エッグカフェ アンバー」 番組「未来予想図」'
        )
        self.assertNotIn("こんにちは", terms)
        self.assertNotIn("いらっしゃいませ", terms)
        self.assertIn("エッグカフェ アンバー", terms)
        self.assertIn("未来予想図", terms)

    def test_unquoted_japanese_person_names_are_dictionary_terms(self):
        terms = extract_terms("店主の名取菜穂子さんと、夫の耀一さん。名取さんご夫婦です。")
        self.assertIn("名取菜穂子", terms)
        self.assertIn("耀一", terms)
        self.assertIn("名取", terms)

    def test_person_names_are_prioritized_with_kana_readings(self):
        entities, aliases = build_person_dictionary([{
            "name": "台本.txt",
            "text": "店主の名取菜穂子さんと、名取さんご夫婦。夫の耀一さん。",
        }])
        self.assertEqual(entities[0]["priority"], "person")
        self.assertIn("名取", [item["canonical"] for item in entities])
        self.assertEqual(set(aliases["名取"]), {"なとり", "ナトリ"})

    def test_parenthetical_person_reading_overrides_inferred_reading(self):
        entities, aliases = build_person_dictionary([{
            "name": "台本.docx",
            "path": "/案件/台本.docx",
            "text": "名取さんと名取菜穂子(なとりなおこ）さんが登場する。菜穂子さんです。",
        }])
        by_name = {item["canonical"]: item for item in entities}
        self.assertEqual(by_name["名取菜穂子"]["reading_source"], "explicit")
        self.assertEqual(set(aliases["名取菜穂子"]), {"なとりなおこ", "ナトリナオコ"})
        self.assertEqual(set(aliases["菜穂子"]), {"なおこ", "ナオコ"})

    def test_premiere_placeholder_segment_is_not_a_completed_transcript(self):
        transcript = {
            "segments": [{
                "start": 905.0,
                "duration": 2000.0,
                "speaker": "00000000-0000-0000-0000-000000000000",
                "words": [],
            }],
            "speakers": [],
        }
        self.assertFalse(transcript_has_words(transcript))

    def test_premiere_transcript_with_text_is_completed(self):
        transcript = {
            "segments": [{
                "start": 0.0,
                "duration": 1.0,
                "words": [{"text": "テストです。"}],
            }],
        }
        self.assertTrue(transcript_has_words(transcript))

    def test_mov_and_mp4_are_video_sources(self):
        self.assertTrue(is_video_source(Path("clip.mov")))
        self.assertTrue(is_video_source(Path("clip.MP4")))
        self.assertFalse(is_video_source(Path("clip.wav")))

    def test_speaker_first_bulk_rename_then_segment_override(self):
        candidates = {"items": [
            {"segment_index": 0, "speaker_name": "話者 1"},
            {"segment_index": 1, "speaker_name": "話者 1"},
            {"segment_index": 2, "speaker_name": "話者 2"},
        ]}
        decisions = {"0": {"status": "approved", "text": "本文"}}
        decisions, changed = update_speaker_decisions(candidates, decisions, {
            "segment_index": 0,
            "speaker_name": "野嶋",
            "original_speaker_name": "話者 1",
            "scope": "bulk",
        })
        self.assertEqual(changed, [0, 1])
        self.assertEqual(decisions["0"]["speaker_name"], "野嶋")
        self.assertEqual(decisions["1"]["speaker_name"], "野嶋")
        self.assertEqual(decisions["0"]["status"], "approved")

        decisions, changed = update_speaker_decisions(candidates, decisions, {
            "segment_index": 1,
            "speaker_name": "スタッフ",
            "original_speaker_name": "話者 1",
            "scope": "segment",
        })
        self.assertEqual(changed, [1])
        self.assertEqual(decisions["0"]["speaker_name"], "野嶋")
        self.assertEqual(decisions["1"]["speaker_name"], "スタッフ")

    def test_speaker_manager_merges_all_used_intervals_and_protects_placeholders(self):
        candidates = {"items": [
            {"segment_index": 0, "speaker_name": "話者 1"},
            {"segment_index": 1, "speaker_name": "話者 1"},
            {"segment_index": 2, "speaker_name": "不明"},
        ]}
        decisions = {
            "0": {"speaker_name": "名取菜保子"},
            "1": {"speaker_name": "名取菜保子", "status": "approved"},
        }
        updated, changed, old_name, new_name = manage_speaker_decisions(
            candidates,
            decisions,
            {"action": "replace", "old_name": "名取菜保子", "new_name": "名取菜穂子"},
        )
        self.assertEqual(changed, [0, 1])
        self.assertEqual((old_name, new_name), ("名取菜保子", "名取菜穂子"))
        self.assertEqual(updated["0"]["speaker_name"], "名取菜穂子")
        self.assertEqual(updated["1"]["status"], "approved")
        with self.assertRaisesRegex(ValueError, "初期話者名"):
            manage_speaker_decisions(
                candidates,
                decisions,
                {"action": "delete", "old_name": "不明", "new_name": ""},
            )
        with self.assertRaisesRegex(ValueError, "置換先"):
            manage_speaker_decisions(
                candidates,
                decisions,
                {"action": "delete", "old_name": "名取菜保子", "new_name": ""},
            )

    def test_dictionary_and_audio_preparation_run_in_parallel(self):
        barrier = threading.Barrier(3)

        def references(_project):
            barrier.wait(timeout=1)
            return {"terms": ["ACME"]}

        def audio(_project):
            barrier.wait(timeout=1)

        def video(_project):
            barrier.wait(timeout=1)

        with patch("speaker_repair.uxp_bridge.build_references", side_effect=references), \
             patch("speaker_repair.uxp_bridge.convert_audio", side_effect=audio), \
             patch("speaker_repair.uxp_bridge.prepare_review_video", side_effect=video):
            result = prepare_inputs_parallel(object())
        self.assertEqual(result["terms"], ["ACME"])

    def test_external_encoder_proxy_waits_in_parallel_with_dictionary_and_audio(self):
        barrier = threading.Barrier(3)

        def references(_project):
            barrier.wait(timeout=1)
            return {"terms": ["DELTA"]}

        def audio(_project):
            barrier.wait(timeout=1)

        def proxy(_project):
            barrier.wait(timeout=1)

        with patch("speaker_repair.uxp_bridge.build_references", side_effect=references), \
             patch("speaker_repair.uxp_bridge.convert_audio", side_effect=audio), \
             patch("speaker_repair.uxp_bridge.wait_for_review_proxy", side_effect=proxy), \
             patch("speaker_repair.uxp_bridge.prepare_review_video") as local_video:
            result = prepare_inputs_parallel(object(), external_review_proxy=True)
        self.assertEqual(result["terms"], ["DELTA"])
        local_video.assert_not_called()

    def test_wait_for_encoder_proxy_requires_a_finished_nonempty_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review-proxy.mp4"
            path.write_bytes(b"x" * 40_000)
            project = type("ReviewProject", (), {"review_video_path": path})()
            self.assertEqual(
                wait_for_review_proxy(project, timeout_seconds=1, poll_seconds=0.001, stable_checks=1),
                path,
            )

    def test_file_bridge_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = FileBridge(Path(directory))
            request = {"id": "health-test", "path": "/api/health", "payload": {}}
            (bridge.inbox / "health-test.json").write_text(
                json.dumps(request), encoding="utf-8"
            )
            self.assertEqual(bridge.process_once(), 1)
            response = json.loads(
                (bridge.outbox / "health-test.json").read_text(encoding="utf-8")
            )
            self.assertTrue(response["ok"])
            self.assertEqual(response["transport"], "file")

    def test_diagnostic_log_is_written_and_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "uxp-errors.jsonl"
            write_diagnostic_log(
                {
                    "source": "test-panel",
                    "context": "scan-selection",
                    "message": "テストエラー",
                    "stack": "stack line",
                    "details": {"asset": "clip.mov"},
                },
                path,
            )
            logs = latest_diagnostic_logs(path)
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0]["message"], "テストエラー")
            self.assertEqual(logs[0]["details"]["asset"], "clip.mov")

    def test_reference_folder_is_scanned_recursively(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "台本.docx").write_bytes(b"placeholder")
            (root / "sub").mkdir()
            (root / "sub" / "資料.pdf").write_bytes(b"placeholder")
            (root / "sub" / "画像.png").write_bytes(b"placeholder")
            self.assertEqual(
                [path.name for path in discover_reference_documents(str(root))],
                ["資料.pdf", "台本.docx"],
            )

    def test_manifest_can_be_built_from_uxp_json_without_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = {
                "language": "ja-jp",
                "segments": [{
                    "speaker": "speaker-1",
                    "start": 1.25,
                    "duration": 2.5,
                    "words": [{"text": "テストです。", "confidence": 1.0}],
                }],
                "speakers": [{"id": "speaker-1", "name": "話者 1"}],
            }
            (root / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
            (root / "media.wav").write_bytes(b"")
            config = {
                "project_name": "UXP test",
                "source_dir": str(root),
                "transcript_json": "transcript.json",
                "source_media": "media.wav",
                "known_speakers": ["話者 1"],
                "analysis_dir": str(root / "analysis"),
            }
            config_path = root / "project.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            manifest = build_manifest(Project.load(config_path))
            self.assertEqual(manifest["segment_count"], 1)
            self.assertEqual(manifest["csv_json_mismatch_indices"], [])
            self.assertEqual(manifest["segments"][0]["premiere_start"], "00:00:01.250")


if __name__ == "__main__":
    unittest.main()
