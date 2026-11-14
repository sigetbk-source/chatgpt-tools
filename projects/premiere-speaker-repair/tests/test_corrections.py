import unittest

from speaker_repair.corrections import (
    build_change_highlights,
    build_corrected_transcript_json,
    build_corrected_transcript_text,
    canonicalize_terms,
    find_identity_candidates,
    find_verification_notes,
    flatten_words,
    is_reliable_whisper_segment,
    is_review_worthy_change,
    merge_transcripts_preserving_premiere,
    restore_original_selection,
    semantic_split_whisper,
)


class CorrectionTests(unittest.TestCase):
    def test_restore_selected_original_text_into_proposal(self):
        result = restore_original_selection("これはデルタ社です", "これはDELTAです", 3, 7)
        self.assertEqual(result["selected_text"], "デルタ社")
        self.assertEqual(result["text"], "これはデルタ社です")

    def test_restore_selection_preserves_insert_outside_selection(self):
        result = restore_original_selection("AB", "A追加B", 1, 2)
        self.assertEqual(result["text"], "A追加B")

    def test_whisper_hallucination_segment_is_excluded(self):
        whisper = {"segments": [
            {
                "text": "６" * 40,
                "compression_ratio": 13.0,
                "avg_logprob": -0.14,
                "words": [{"word": "６", "start": 0, "end": 20}],
            },
            {
                "text": "2、3m歩いて",
                "compression_ratio": 1.7,
                "avg_logprob": -0.3,
                "words": [{"word": "2、3m歩いて", "start": 20, "end": 22}],
            },
        ]}
        self.assertEqual(
            [word["word"] for word in flatten_words(whisper)],
            ["2、3m歩いて"],
        )

    def test_legitimate_countdown_is_not_treated_as_hallucination(self):
        self.assertTrue(is_reliable_whisper_segment({
            "text": "5、4、3、2、1",
            "compression_ratio": 1.9,
            "avg_logprob": -0.4,
        }))

    def test_build_corrected_json_preserves_speaker_and_timing(self):
        source = {
            "segments": [{
                "speaker": "speaker-id",
                "start": 1.0,
                "duration": 2.0,
                "words": [
                    {"text": "デルタ社", "start": 1.0, "duration": 1.0, "eos": False},
                    {"text": "です。", "start": 2.0, "duration": 1.0, "eos": True},
                ],
            }],
            "speakers": [{"id": "speaker-id", "name": "野嶋"}],
        }
        output = build_corrected_transcript_json(source, ["DELTAです。"])
        self.assertEqual(output["segments"][0]["speaker"], "speaker-id")
        self.assertEqual(output["segments"][0]["start"], 1.0)
        self.assertEqual(
            "".join(word["text"] for word in output["segments"][0]["words"]),
            "DELTAです。",
        )
        self.assertEqual(source["segments"][0]["words"][0]["text"], "デルタ社")

    def test_corrected_json_preserves_empty_word_timing_slots(self):
        source = {
            "segments": [{
                "speaker": "speaker-id", "start": 0, "duration": 2,
                "words": [
                    {"text": "元", "start": 0, "duration": 1},
                    {"text": "", "start": 1, "duration": 1, "tags": ["disfluency"]},
                ],
            }],
            "speakers": [{"id": "speaker-id", "name": "話者"}],
        }
        output = build_corrected_transcript_json(source, ["修正"])
        self.assertEqual(len(output["segments"][0]["words"]), 2)
        self.assertEqual(output["segments"][0]["words"][1]["start"], 1)
        self.assertEqual(output["segments"][0]["words"][1]["tags"], ["disfluency"])

    def test_build_corrected_json_can_change_speaker_name(self):
        source = {
            "segments": [{
                "speaker": "old-id", "start": 0, "duration": 1,
                "words": [{"text": "はい。", "start": 0, "duration": 1}],
            }],
            "speakers": [{"id": "old-id", "name": "話者 1"}],
        }
        output = build_corrected_transcript_json(source, ["はい。"], ["野嶋"])
        speaker_id = output["segments"][0]["speaker"]
        self.assertEqual(
            next(item["name"] for item in output["speakers"] if item["id"] == speaker_id),
            "野嶋",
        )

    def test_build_corrected_transcript_text_preserves_sections(self):
        source = "00:00:00:00 - 00:00:01:00\n話者 1\n元の文。\n\n00:00:01:00 - 00:00:02:00\n話者 2\n次の文。\n"
        output = build_corrected_transcript_text(source, ["修正した文。", "次の文。"])
        self.assertEqual(
            output,
            "00:00:00:00 - 00:00:01:00\n話者 1\n修正した文。\n\n00:00:01:00 - 00:00:02:00\n話者 2\n次の文。\n",
        )

    def test_canonicalize_terms(self):
        text, applied = canonicalize_terms("アクメのSAAS。", {"ACME": ("アクメ",), "SaaS": ("SAAS",)})
        self.assertEqual(text, "ACMEのSaaS。")
        self.assertEqual(len(applied), 2)

    def test_canonicalize_ascii_aliases_ignores_case(self):
        text, applied = canonicalize_terms(
            "DeltaさんとSyncさんのB2B番組",
            {"DELTA": ("デルタ社",), "ACME": ("SYNC",), "BtoB": ("B2B",)},
        )
        self.assertEqual(text, "DELTAさんとACMEさんのBtoB番組")
        self.assertEqual(len(applied), 3)

    def test_canonicalize_prefers_longer_alias(self):
        text, _ = canonicalize_terms(
            "アクメソンとデルタ社",
            {"ACME": ("アクメ",), "ACMEさん": ("アクメソン",), "DELTA": ("デルタ社",)},
        )
        self.assertEqual(text, "ACMEさんとDELTA")

    def test_change_highlights_separate_reference_and_whisper_provenance(self):
        highlights = build_change_highlights(
            "アクメは便利です。",
            "SYNCはとても便利です。",
            "ACMEはとても便利です。",
            [{"from": "SYNC", "to": "ACME"}],
        )
        proposal = "ACMEはとても便利です。"
        original = "アクメは便利です。"
        whisper = "SYNCはとても便利です。"
        proposal_by_kind = {
            kind: "".join(
                proposal[item["start"]:item["end"]]
                for item in highlights["proposal"] if item["kind"] == kind
            )
            for kind in ("reference", "whisper")
        }
        self.assertIn("ACME", proposal_by_kind["reference"])
        self.assertIn("とても", proposal_by_kind["whisper"])
        self.assertTrue(any(
            original[item["start"]:item["end"]]
            for item in highlights["premiere"]
        ))
        self.assertTrue(any(
            whisper[item["start"]:item["end"]] == "SYNC"
            and item["kind"] == "reference-source"
            for item in highlights["whisper"]
        ))
        self.assertTrue(any(
            "とても" in whisper[item["start"]:item["end"]]
            and item["kind"] == "source"
            for item in highlights["whisper"]
        ))

    def test_whisper_highlight_excludes_unadopted_source_difference(self):
        highlights = build_change_highlights(
            "残す文です。",
            "削る文です。追加します。",
            "残す文です。",
        )
        self.assertEqual(highlights["proposal"], [])
        self.assertEqual(highlights["whisper"], [])

    def test_whisper_addition_backed_by_reference_is_orange(self):
        original = "きっとこのお店でしょう。アンバー卵です。"
        whisper = "きっとこのお店でしょう。エッグカフェ、アンバー。卵です。"
        proposal = whisper
        highlights = build_change_highlights(
            original,
            whisper,
            proposal,
            [],
            ["エッグカフェ アンバー"],
        )
        orange_proposal = "".join(
            proposal[item["start"]:item["end"]]
            for item in highlights["proposal"]
            if item["kind"] == "reference"
        )
        orange_source = "".join(
            whisper[item["start"]:item["end"]]
            for item in highlights["whisper"]
            if item["kind"] == "reference-source"
        )
        self.assertIn("エッグカフェ、アンバー", orange_proposal)
        self.assertIn("エッグカフェ、アンバー", orange_source)

    def test_change_highlights_do_not_mark_unchanged_repeated_reference_term(self):
        highlights = build_change_highlights(
            "DELTAとデルタ社",
            "DELTAとデルタ社",
            "DELTAとDELTA",
            [{"from": "デルタ社", "to": "DELTA"}],
        )
        reference_ranges = [
            item for item in highlights["proposal"] if item["kind"] == "reference"
        ]
        self.assertEqual(reference_ranges, [{"start": 6, "end": 11, "kind": "reference"}])

    def test_change_highlights_mark_all_changed_occurrences_of_reference_term(self):
        proposal = "商品A、商品Aです。"
        highlights = build_change_highlights(
            "案内です。",
            proposal,
            proposal,
            [],
            ["商品A"],
        )
        reference_texts = [
            proposal[item["start"]:item["end"]]
            for item in highlights["proposal"]
            if item["kind"] == "reference"
        ]
        self.assertEqual(reference_texts, ["商品A", "商品A"])

    def test_identity_candidate_does_not_rewrite_transcript(self):
        hints = ({
            "mentions": ["足立さん"],
            "category": "人物",
            "candidate": "足立光",
            "confidence": "高",
            "reason": "西口一希との共著・対談歴",
        },)
        matches = find_identity_candidates("西口一希さんとか足立さんとか", hints)
        self.assertEqual(matches[0]["candidate"], "足立光")
        self.assertEqual(matches[0]["mention"], "足立さん")
        self.assertIn("追加しない", matches[0]["transcript_policy"])

    def test_verification_note_records_whisper_only_origin(self):
        hints = ({
            "term": "N1インタビュー",
            "status": "公開情報で検証済み",
            "judgment": "表記を維持",
        },)
        notes = find_verification_notes(
            "西口さんのインタビュー",
            "西口さんのN1インタビュー",
            "西口一希さんのN1インタビュー",
            hints,
        )
        self.assertEqual(notes[0]["origins"], ["Whisper補完"])
        self.assertEqual(notes[0]["judgment"], "表記を維持")

    def test_one_character_short_sentence_change_is_suppressed(self):
        self.assertFalse(is_review_worthy_change(
            "泥臭い人たち、結構多そうですね。",
            "泥臭い人たち、結構多いそうですね。",
        ))

    def test_punctuation_only_change_is_suppressed(self):
        self.assertFalse(is_review_worthy_change("今日は晴れです", "今日は、晴れです。"))

    def test_spoken_contraction_change_is_suppressed(self):
        self.assertFalse(is_review_worthy_change(
            "必要だと思ってます。", "必要だと思っています。"
        ))
        self.assertFalse(is_review_worthy_change(
            "いま確認してるところです。", "いま確認しているところです。"
        ))

    def test_spoken_sentence_final_particle_is_retained(self):
        self.assertTrue(is_review_worthy_change("そうです。", "そうですね。"))
        self.assertTrue(is_review_worthy_change("大丈夫です。", "大丈夫ですか。"))

    def test_material_backed_one_character_change_is_retained(self):
        self.assertTrue(is_review_worthy_change(
            "野島さんです", "野嶋さんです", has_evidence=True
        ))

    def test_two_character_content_recovery_is_retained(self):
        self.assertTrue(is_review_worthy_change(
            "ブランド認識です", "AIブランド認識です"
        ))

    def test_semantic_split_uses_text_boundaries(self):
        segments = [{"text": "こんにちは"}, {"text": "山田です"}]
        words = [
            {"word": "こんにちは、", "start": 0, "end": 1},
            {"word": "山田です。", "start": 1, "end": 2},
        ]
        self.assertEqual(semantic_split_whisper(segments, words), ["こんにちは", "、山田です"])

    def test_semantic_split_snaps_sentence_ending_around_repeated_words(self):
        segments = [
            {"text": "お二方をご紹介します。まずは言い直します。"},
            {"text": "まずはACMEの佐藤さんです。"},
        ]
        words = [
            {"word": "お二方をご紹介します。", "start": 0, "end": 1},
            {"word": "まずはACMEの佐藤さんです。", "start": 1, "end": 2},
        ]
        self.assertEqual(
            semantic_split_whisper(segments, words),
            ["お二方をご紹介します。", "まずはACMEの佐藤さんです"],
        )

    def test_merge_preserves_false_starts_and_premiere_only_words(self):
        original = "まずはこの方です。まずは新コンサル部の佐藤貴紀さんです。"
        whisper = "まずはアクメコンサル部の田中一郎さんです。よろしくお願いします。"
        self.assertEqual(
            merge_transcripts_preserving_premiere(original, whisper),
            "まずはこの方です。まずはアクメコンサル部の田中一郎さんです。よろしくお願いします。",
        )

    def test_merge_adds_words_only_whisper_heard(self):
        original = "これはブランドが何かしら認識を持っています。"
        whisper = "これはブランド、AIが何かしら認識を持っています。"
        self.assertEqual(
            merge_transcripts_preserving_premiere(original, whisper),
            "これはブランド、AIが何かしら認識を持っています。",
        )


if __name__ == "__main__":
    unittest.main()
