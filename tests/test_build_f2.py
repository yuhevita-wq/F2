"""F2 source filters and disjoint source periods; offline-only tests."""
import unittest
from scripts.build_f2 import eligible, load_manifest


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.a12 = {"id": "A12-test", "class": "A12",
                    "start_date": "2025-01-01", "end_date": "2025-02-28"}
        self.a3 = {"id": "A3-test", "class": "A3",
                   "start_date": "2025-01-01", "end_date": "2025-02-28"}
        self.a = {"race_id": "x20250101", "race_date": "2025-01-01",
                  "meeting_grade": "F2", "race_type": "Ａ級予選",
                  "class_scope": "A1/A2"}
        self.challenge = {"race_id": "y20250101", "race_date": "2025-01-01",
                          "meeting_grade": "F2",
                          "race_type": "チャレンジ予選",
                          "class_scope": "A3 challenge"}

    def test_a12_f2_kept(self):
        self.assertTrue(eligible(self.a, self.a12))

    def test_a12_f1_excluded(self):
        self.assertFalse(eligible({**self.a, "meeting_grade": "F1"}, self.a12))

    def test_unknown_grade_excluded(self):
        self.assertFalse(eligible({**self.a, "meeting_grade": "unknown"}, self.a12))

    def test_fullwidth_f2_kept(self):
        self.assertTrue(eligible({**self.a, "meeting_grade": "Ｆ２"}, self.a12))

    def test_a3_challenge_f2_kept(self):
        self.assertTrue(eligible(self.challenge, self.a3))

    def test_a3_g3_mixed_source_is_excluded(self):
        self.assertFalse(eligible({**self.challenge, "meeting_grade": "G3"}, self.a3))

    def test_a3_non_challenge_rejected(self):
        with self.assertRaises(ValueError):
            eligible({**self.challenge, "race_type": "Ａ級予選"}, self.a3)

    def test_a12_class_scope_conflict_rejected(self):
        with self.assertRaises(ValueError):
            eligible({**self.a, "class_scope": "A3 challenge"}, self.a12)

    def test_a3_class_scope_conflict_rejected(self):
        with self.assertRaises(ValueError):
            eligible({**self.challenge, "class_scope": "A1/A2"}, self.a3)

    def test_outside_period_rejected(self):
        with self.assertRaises(ValueError):
            eligible({**self.challenge, "race_date": "2024-12-31"}, self.a3)

    def test_manifest_disjoint(self):
        m = load_manifest()
        self.assertEqual(len(m["sources"]), 22)
        self.assertEqual(
            {(s["year"], s["class"]) for s in m["sources"]},
            {(2025, "A12"), (2025, "A3"), (2026, "A12"), (2026, "A3")})
        self.assertEqual(len(m["source_ref"]), 40)


if __name__ == "__main__":
    unittest.main()
