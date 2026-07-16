"""Tests for the LongMemEval benchmark harness."""
import importlib.util
import json
import os
from pathlib import Path
import sys
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
        self.assertEqual(
            len(metrics["selected_question_ids"]), 1)
        self.assertEqual(
            len(metrics["evaluated_question_ids"]), 1)

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

    def test_bm25_baseline_uses_same_evidence_mapping(self):
        instances = [{
            "question_id": "q-bm25",
            "question": "where are backup snapshots kept",
            "answer": "helsinki",
            "answer_session_ids": ["s1"],
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": ["2026-01-01", "2026-01-02"],
            "haystack_sessions": [
                [{
                    "role": "assistant",
                    "content": "backup snapshots are kept in helsinki",
                    "has_answer": True,
                }],
                [{
                    "role": "assistant",
                    "content": "release errors go to sentry",
                }],
            ],
        }]

        metrics = LME.evaluate(
            instances, limit=1, top_k=1, engine="bm25")

        self.assertEqual(metrics["backend"]["mode"], "bm25")
        self.assertEqual(metrics["evidence_at_1_rate"], 1.0)
        self.assertEqual(metrics["answer_string_at_k_rate"], 1.0)

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

    def test_non_positive_limit_and_top_k_are_usage_errors(self):
        for args in (["--limit", "0"], ["--limit", "-1"],
                     ["--top-k", "0"], ["--top-k", "-1"]):
            with self.assertRaises(SystemExit) as raised:
                LME.parse_args(args)
            self.assertEqual(raised.exception.code, 2)

    def test_bm25_rejects_embedding_options(self):
        with self.assertRaises(SystemExit) as raised:
            LME.parse_args([
                "--engine", "bm25",
                "--embed-server", "python3 server.py",
            ])
        self.assertEqual(raised.exception.code, 2)

    def test_digest_mismatch_is_rejected(self):
        fixture = ROOT / "tests" / "fixtures" / "longmemeval_tiny.json"
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            LME.resolve_data(
                str(fixture), Path(tempfile.gettempdir()), "0" * 64)

    def test_ambient_embedding_environment_is_scrubbed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            marker = tmp / "called"
            script = tmp / "unexpected_embedder.py"
            script.write_text(
                "import pathlib, sys\n"
                "pathlib.Path(sys.argv[1]).write_text('called')\n"
                "print('[1, 0]')\n",
                encoding="utf-8",
            )
            previous = os.environ.get("MIND_EMBED_CMD")
            os.environ["MIND_EMBED_CMD"] = "%s %s %s" % (
                sys.executable, script, marker)
            try:
                metrics = LME.evaluate(
                    self.fixture(), limit=2, seed=1, top_k=5)
            finally:
                if previous is None:
                    os.environ.pop("MIND_EMBED_CMD", None)
                else:
                    os.environ["MIND_EMBED_CMD"] = previous

        self.assertFalse(marker.exists())
        self.assertEqual(metrics["backend"]["mode"], "offline")
        self.assertEqual(metrics["backend"]["calls"], 0)

    def test_explicit_batch_backend_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "batch_embedder.py"
            script.write_text(
                "import json, sys\n"
                "request = json.load(sys.stdin)\n"
                "vectors = [[1.0, float(i + 1)]\n"
                "           for i, _ in enumerate(request['texts'])]\n"
                "json.dump({'protocol': 'mind-embed-v1',\n"
                "           'model': 'longmem-fixture',\n"
                "           'vectors': vectors}, sys.stdout)\n",
                encoding="utf-8",
            )
            metrics = LME.evaluate(
                self.fixture(),
                limit=2,
                seed=1,
                top_k=5,
                embed_cmd="%s %s" % (sys.executable, script),
                require_embed=True,
            )

        self.assertEqual(metrics["backend"]["mode"], "command")
        self.assertEqual(metrics["backend"]["fallbacks"], 0)
        self.assertGreater(metrics["backend"]["calls"], 0)
        self.assertEqual(metrics["backend"]["models"],
                         ["longmem-fixture"])

    def test_question_ingest_uses_one_graph_commit(self):
        instance = {
            "question_id": "q-bulk",
            "answer_session_ids": ["s1"],
            "haystack_session_ids": ["s1"],
            "haystack_dates": ["2026-01-01"],
            "haystack_sessions": [[
                {"role": "user", "content": "first turn"},
                {
                    "role": "assistant",
                    "content": "answer turn",
                    "has_answer": True,
                },
            ]],
        }
        with tempfile.TemporaryDirectory() as temporary:
            hippo = LME.Hippocampus(
                Path(temporary) / "graph.json")
            calls = [0]
            original = hippo._commit_current

            def counted_commit():
                calls[0] += 1
                return original()

            hippo._commit_current = counted_commit
            evidence, total = LME.remember_instance(
                instance, hippo, "turn")

        self.assertEqual(total, 2)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(calls[0], 1)

    def test_explicit_persistent_server_is_recorded(self):
        server = "%s %s" % (
            sys.executable,
            ROOT / "contrib" / "concept_embed_server.py",
        )
        metrics = LME.evaluate(
            self.fixture(),
            limit=2,
            seed=1,
            top_k=5,
            embed_server=server,
            require_embed=True,
        )

        self.assertEqual(metrics["backend"]["mode"], "server")
        self.assertEqual(metrics["backend"]["fallbacks"], 0)
        self.assertGreater(metrics["backend"]["calls"], 0)
        self.assertEqual(
            metrics["backend"]["models"],
            ["stdlib-concept-hash@2"],
        )

    def test_default_manifest_is_immutable_and_digest_pinned(self):
        manifest = LME.load_manifest()

        self.assertNotIn("/resolve/main/", manifest["url"])
        self.assertIn(manifest["revision"], manifest["url"])
        self.assertRegex(manifest["sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
