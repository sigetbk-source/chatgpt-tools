import unittest

from magic_data import derive_events, display_magic_values, validate_payload


class MagicDataTests(unittest.TestCase):
    def load(self, data):
        return validate_payload({"data": data})

    def test_off_day_can_reduce_magic(self):
        payload = self.load([
            {"date": "2026-08-05", "magic": 37, "opponent": "F"},
            {"date": "2026-08-06", "magic": 36, "opponent": None},
        ])
        self.assertEqual(payload["data"][-1]["magic"], 36)

    def test_extinguish_and_relight_reset_monotonic_segment(self):
        points = [
            {"date": "2026-08-05", "magic": 37, "opponent": "F"},
            {"date": "2026-08-06", "magic": None, "opponent": "F"},
            {"date": "2026-08-07", "magic": 36, "opponent": None},
        ]
        self.load(points)
        self.assertEqual(derive_events(points), {0: "lit", 1: "extinguished", 2: "relit"})

    def test_m0_is_champion_event(self):
        points = [
            {"date": "2026-08-05", "magic": 2, "opponent": "F"},
            {"date": "2026-08-06", "magic": 1, "opponent": None},
            {"date": "2026-08-07", "magic": 0, "opponent": "L"},
            {"date": "2026-08-08", "magic": 0, "opponent": None},
        ]
        self.load(points)
        self.assertEqual(derive_events(points), {0: "lit", 2: "champion"})

    def test_m0_after_extinction_is_champion_not_relight(self):
        points = [
            {"date": "2026-08-05", "magic": 2, "opponent": "F"},
            {"date": "2026-08-06", "magic": None, "opponent": "F"},
            {"date": "2026-08-07", "magic": 0, "opponent": None},
        ]
        self.load(points)
        self.assertEqual(derive_events(points), {0: "lit", 1: "extinguished", 2: "champion"})

    def test_relight_must_be_lower_than_pre_extinction_value(self):
        with self.assertRaisesRegex(ValueError, "relit magic must be lower"):
            self.load([
                {"date": "2026-08-05", "magic": 37, "opponent": "F"},
                {"date": "2026-08-06", "magic": None, "opponent": "F"},
                {"date": "2026-08-07", "magic": 37, "opponent": None},
            ])

    def test_extinction_placement_carries_last_active_value(self):
        points = [
            {"date": "2026-08-05", "magic": 37, "opponent": "F"},
            {"date": "2026-08-06", "magic": None, "opponent": "F"},
            {"date": "2026-08-07", "magic": None, "opponent": "F"},
            {"date": "2026-08-08", "magic": 36, "opponent": None},
        ]
        self.assertEqual(display_magic_values(points), [37, 37, 37, 36])

    def test_increase_while_continuously_lit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not increase"):
            self.load([
                {"date": "2026-08-05", "magic": 37, "opponent": "F"},
                {"date": "2026-08-06", "magic": 38, "opponent": None},
            ])


if __name__ == "__main__":
    unittest.main()
