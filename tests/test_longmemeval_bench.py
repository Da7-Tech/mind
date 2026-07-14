"""Tests for the LongMemEval benchmark harness."""
import importlib.util
from pathlib import Path
import tempfile
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
        self.assertEqual(metrics["evidence_at_1_rate"], 0.0)
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
        self.assertEqual(metrics["memory_records"], 4)
        self.assertEqual(metrics["avg_memory_records"], 4.0)

    def test_turn_granularity_uses_exact_marked_turns(self):
        instance = {
            "question_id": "q1",
            "answer_session_ids": ["s1"],
            "haystack_session_ids": ["s1"],
            "haystack_dates": ["2026-01-01"],
            "haystack_sessions": [[
                {"role": "user", "content": "irrelevant before"},
                {"role": "assistant", "content": "answer is Kyoto", "has_answer": True},
                {"role": "user", "content": "irrelevant after"},
            ]],
        }
        with tempfile.TemporaryDirectory() as tmp:
            h = LME.Hippocampus(Path(tmp) / "graph.json")
            evidence, total = LME.remember_instance(instance, h, "turn")

        self.assertEqual(total, 3)
        self.assertEqual(len(evidence), 1)
        self.assertIn("answer is Kyoto", h.nodes[next(iter(evidence))]["text"])

    def test_turn_granularity_falls_back_for_unmarked_answer_session(self):
        instance = {
            "question_id": "q1",
            "answer_session_ids": ["s1"],
            "haystack_session_ids": ["s1"],
            "haystack_dates": ["2026-01-01"],
            "haystack_sessions": [[
                {"role": "user", "content": "first answer-session turn"},
                {"role": "assistant", "content": "second answer-session turn"},
            ]],
        }
        with tempfile.TemporaryDirectory() as tmp:
            h = LME.Hippocampus(Path(tmp) / "graph.json")
            evidence, total = LME.remember_instance(instance, h, "turn")

        self.assertEqual(total, 2)
        self.assertEqual(len(evidence), 2)


if __name__ == "__main__":
    unittest.main()
