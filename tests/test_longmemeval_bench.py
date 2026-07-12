"""Tests for the LongMemEval benchmark harness."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "longmemeval_bench", ROOT / "bench" / "longmemeval.py")
LME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LME)


class TestLongMemEvalBench(unittest.TestCase):
    def fixture(self):
        return LME.load_instances(ROOT / "tests" / "fixtures" / "longmemeval_tiny.json")

    def test_evaluate_tiny_fixture(self):
        metrics = LME.evaluate(self.fixture(), limit=2, seed=1, top_k=5)

        self.assertEqual(metrics["evaluated"], 1)
        self.assertEqual(metrics["skipped_abstention"], 1)
        self.assertEqual(metrics["skipped_no_evidence"], 0)
        self.assertEqual(metrics["evidence_at_1_rate"], 1.0)
        self.assertEqual(metrics["evidence_at_k_rate"], 1.0)
        self.assertEqual(metrics["answer_string_at_k_rate"], 1.0)

    def test_session_granularity_evaluates(self):
        metrics = LME.evaluate(
            self.fixture(),
            limit=2,
            seed=1,
            top_k=5,
            granularity="session",
        )

        self.assertEqual(metrics["evaluated"], 1)
        self.assertEqual(metrics["memory_records"], 2)
        self.assertEqual(metrics["evidence_at_k_rate"], 1.0)

    def test_include_abstention_leaves_no_evidence_skipped(self):
        metrics = LME.evaluate(
            self.fixture(),
            limit=2,
            seed=1,
            top_k=5,
            include_abstention=True,
        )

        self.assertEqual(metrics["evaluated"], 1)
        self.assertEqual(metrics["skipped_abstention"], 0)
        self.assertEqual(metrics["skipped_no_evidence"], 1)


if __name__ == "__main__":
    unittest.main()
