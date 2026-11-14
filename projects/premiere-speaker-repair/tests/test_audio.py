import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from speaker_repair.audio import chunk_ranges, prepare_review_video


class ChunkRangeTests(unittest.TestCase):
    def test_chunk_ranges_avoids_short_tail(self):
        self.assertEqual(
            list(chunk_ranges(5, 22, 2.5, 10)),
            [(5, 10), (15, 6), (21, 6)],
        )

    def test_short_segment_is_skipped(self):
        self.assertEqual(list(chunk_ranges(0, 1, 2.5, 10) or []), [])

    def test_review_video_proxy_is_browser_compatible_and_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mov"
            source.write_bytes(b"source")
            project = SimpleNamespace(
                audio=source,
                analysis_dir=root / "analysis",
                review_video_path=root / "analysis" / "review-proxy.mp4",
                audio_stream_index=None,
            )

            def create_output(command, check):
                self.assertTrue(check)
                Path(command[-1]).write_bytes(b"proxy")

            with patch("speaker_repair.audio.ffmpeg_executable", return_value="ffmpeg"), \
                 patch("speaker_repair.audio.subprocess.run", side_effect=create_output) as run:
                output = prepare_review_video(project)
                cached = prepare_review_video(project)
            self.assertEqual(output, project.review_video_path)
            self.assertEqual(cached, project.review_video_path)
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertIn("h264_videotoolbox", command)
            self.assertIn("yuv420p", command)


if __name__ == "__main__":
    unittest.main()
