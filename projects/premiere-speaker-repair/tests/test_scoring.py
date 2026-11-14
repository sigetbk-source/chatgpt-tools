import unittest

import numpy as np

from speaker_repair.scoring import cosine, score_chunks


class ScoringTests(unittest.TestCase):
    def test_cosine(self):
        self.assertEqual(cosine(np.array([1.0, 0.0]), np.array([1.0, 0.0])), 1.0)

    def test_known_speaker_candidate(self):
        chunks = [
            {"segment_index": 0, "chunk_index": 0, "speaker_id": "a", "speaker_name": "A", "locked": True, "embedding": np.array([1.0, 0.0])},
            {"segment_index": 1, "chunk_index": 0, "speaker_id": "b", "speaker_name": "B", "locked": True, "embedding": np.array([0.0, 1.0])},
            {"segment_index": 2, "chunk_index": 0, "speaker_id": "x", "speaker_name": "話者1", "locked": False, "embedding": np.array([0.9, 0.1])},
        ]
        result = score_chunks(chunks, ["A", "B"])
        suggestion = next(x for x in result["segment_suggestions"] if x["segment_index"] == 2)
        self.assertEqual(suggestion["candidate"], "A")
        self.assertEqual(suggestion["confidence"], "strong")


if __name__ == "__main__":
    unittest.main()
