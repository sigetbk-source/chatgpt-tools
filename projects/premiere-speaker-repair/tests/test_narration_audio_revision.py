from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[2] / "ナレーションPremiereハブ_作業" / "runtime"
SPEC = importlib.util.spec_from_file_location("narration_audio_revision_under_test", RUNTIME / "audio_revision.py")
assert SPEC and SPEC.loader
audio_revision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audio_revision)


class NarrationAudioRevisionTests(unittest.TestCase):
    def test_reuses_audio_only_when_audio_caption_and_transcript_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            previous_audio = root / "previous.wav"
            transcript = root / "previous.json"
            source.write_bytes(b"same-wave")
            previous_audio.write_bytes(b"same-wave")
            transcript.write_text("{}", encoding="utf-8")
            previous = {
                "audioPath": str(previous_audio),
                "transcriptPath": str(transcript),
                "captionText": "同じ本文。",
            }
            self.assertTrue(audio_revision.can_reuse_previous_media(source, previous, "同じ本文。"))
            self.assertFalse(audio_revision.can_reuse_previous_media(source, previous, "変更した本文。"))

    def test_does_not_reuse_changed_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            previous_audio = root / "previous.wav"
            transcript = root / "previous.json"
            source.write_bytes(b"new-wave")
            previous_audio.write_bytes(b"old-wave")
            transcript.write_text("{}", encoding="utf-8")
            previous = {
                "audioPath": str(previous_audio),
                "transcriptPath": str(transcript),
                "captionText": "本文。",
            }
            self.assertFalse(audio_revision.can_reuse_previous_media(source, previous, "本文。"))


if __name__ == "__main__":
    unittest.main()
