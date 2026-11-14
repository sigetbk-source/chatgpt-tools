import unittest

from speaker_repair.server import select_bulk_suggestions


class BulkSelectionTests(unittest.TestCase):
    def test_only_strong_non_change_suggestions_are_selected(self):
        analysis = {"segment_suggestions": [
            {"segment_index": 1, "speaker_id": "x", "candidate": "A", "confidence": "strong", "locked": False, "possible_internal_change": False},
            {"segment_index": 2, "speaker_id": "x", "candidate": "A", "confidence": "review", "locked": False, "possible_internal_change": False},
            {"segment_index": 3, "speaker_id": "x", "candidate": "A", "confidence": "strong", "locked": False, "possible_internal_change": True},
            {"segment_index": 4, "speaker_id": "y", "candidate": "A", "confidence": "strong", "locked": False, "possible_internal_change": False},
        ]}
        selected = select_bulk_suggestions(analysis, "x", "A")
        self.assertEqual([item["segment_index"] for item in selected], [1])


if __name__ == "__main__":
    unittest.main()
