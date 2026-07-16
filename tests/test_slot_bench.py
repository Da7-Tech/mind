"""Acceptance test for the labeled slot-conflict benchmark."""
import unittest

from bench import slots


class SlotBenchmarkTests(unittest.TestCase):
    def test_labeled_fifty_pair_gate(self):
        report = slots.evaluate()

        self.assertEqual(report["cases"], 50)
        self.assertGreaterEqual(report["precision"], 0.8)
        self.assertGreaterEqual(report["recall"], 0.8)


if __name__ == "__main__":
    unittest.main()
