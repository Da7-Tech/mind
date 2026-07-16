"""Self-tests for the mutation harness that certifies the unit suite."""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "mutation_bench", ROOT / "bench" / "mutate.py")
MUTATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MUTATE)


class TestMutationBench(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mind-mutate-test-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_staged_workspace_contains_every_test_dependency(self):
        MUTATE.prepare_workspace(self.tmp)

        self.assertTrue((self.tmp / "mind.py").is_file())
        self.assertTrue((self.tmp / "tests").is_dir())
        self.assertTrue((self.tmp / "bench" / "longmemeval.py").is_file())
        self.assertTrue((self.tmp / "src" / "mind").is_dir())
        self.assertTrue((self.tmp / "tools" / "build_single.py").is_file())
        self.assertTrue(
            (self.tmp / "contrib" / "concept_embed_server.py").is_file())

    def test_report_binds_versioned_corpus_and_manifest(self):
        source = (ROOT / "mind.py").read_text("utf-8")
        mutated, applied = MUTATE.make_mutant(source, 0)
        self.assertIsNotNone(applied)
        rows = [{
            "sequence": 1,
            "target": 0,
            "line": applied[0],
            "mutation": applied[1],
        }]
        digest = MUTATE.source_sha256(json.dumps(
            rows, sort_keys=True, separators=(",", ":")))
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(mutated, source)

    def test_mutated_artifact_and_modular_source_stay_in_sync(self):
        MUTATE.prepare_workspace(self.tmp)
        source = (self.tmp / "mind.py").read_text("utf-8")
        MUTATE._sync_modular_mutant(self.tmp, source)

        result = subprocess.run(
            [sys.executable, "tools/build_single.py", "--check"],
            cwd=self.tmp, capture_output=True, text=True)

        self.assertEqual(
            result.returncode, 0, result.stdout + result.stderr)
        manifest = json.loads(
            (self.tmp / "src" / "mind" / "source.json")
            .read_text("utf-8"))
        self.assertEqual(len(manifest["fragments"]), 10)

    def test_red_baseline_aborts_without_classifying_mutants(self):
        report_path = self.tmp / "report.json"
        original = MUTATE.run_suite

        def red_baseline(_workdir, timeout=MUTATE.DEFAULT_TIMEOUT):
            return {
                "outcome": "infrastructure_error",
                "returncode": 1,
                "duration_ms": 1.0,
                "stdout": "",
                "stderr": "planted baseline failure",
                "failing_tests": [],
            }

        MUTATE.run_suite = red_baseline
        try:
            code = MUTATE.main([
                "--sample", "1",
                "--json-out", str(report_path),
            ])
        finally:
            MUTATE.run_suite = original

        report = json.loads(report_path.read_text("utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(report["baseline"]["outcome"],
                         "infrastructure_error")
        self.assertEqual(report["mutants"], [])

    def test_timeout_is_not_counted_as_a_kill(self):
        tests = self.tmp / "tests"
        tests.mkdir()
        (tests / "test_sleep.py").write_text(
            "import time, unittest\n"
            "class Slow(unittest.TestCase):\n"
            "    def test_slow(self):\n"
            "        time.sleep(2)\n",
            encoding="utf-8",
        )

        result = MUTATE.run_suite(self.tmp, timeout=0.1)

        self.assertEqual(result["outcome"], "timed_out")

    def test_failing_test_names_are_preserved(self):
        tests = self.tmp / "tests"
        tests.mkdir()
        (tests / "test_failure.py").write_text(
            "import unittest\n"
            "class Failure(unittest.TestCase):\n"
            "    def test_planted(self):\n"
            "        self.assertEqual(1, 2)\n",
            encoding="utf-8",
        )

        result = MUTATE.run_suite(self.tmp, timeout=5)

        self.assertEqual(result["outcome"], "killed")
        self.assertTrue(result["failing_tests"])
        self.assertNotIn(str(self.tmp), result["stderr"])
        self.assertIn("<workspace>", result["stderr"])


if __name__ == "__main__":
    unittest.main()
