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
        self.assertTrue((self.tmp / ".gitattributes").is_file())
        self.assertTrue((self.tmp / "tests").is_dir())
        self.assertTrue((self.tmp / "bench" / "longmemeval.py").is_file())
        self.assertTrue((self.tmp / "src" / "mind").is_dir())
        self.assertTrue((self.tmp / "tools" / "build_single.py").is_file())
        self.assertTrue(
            (self.tmp / "contrib" / "concept_embed_server.py").is_file())
        self.assertTrue(
            (self.tmp / "docs" / "clients.json").is_file())

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

    def test_zero_float_mutation_is_not_equivalent(self):
        source = "VALUE = 0.0\n"

        mutated, applied = MUTATE.make_mutant(source, 0)

        self.assertEqual(applied, (1, "0.0 -> 1.0"))
        self.assertNotEqual(mutated, source)
        namespace = {}
        exec(compile(mutated, "<mutant>", "exec"), namespace)
        self.assertEqual(namespace["VALUE"], 1.0)

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

    def test_mutation_suite_excludes_self_referential_claims_only(self):
        tests = self.tmp / "tests"
        tests.mkdir()
        (tests / "test_claims.py").write_text(
            "import unittest\n"
            "class Claims(unittest.TestCase):\n"
            "    def test_report_already_exists(self):\n"
            "        self.fail('report is still being generated')\n",
            encoding="utf-8",
        )
        (tests / "test_product.py").write_text(
            "import unittest\n"
            "class Product(unittest.TestCase):\n"
            "    def test_behavior(self):\n"
            "        self.assertEqual(2 + 2, 4)\n",
            encoding="utf-8",
        )

        result = MUTATE.run_suite(self.tmp, timeout=5)

        self.assertEqual(result["outcome"], "survived")
        self.assertEqual(result["tests_run"], 1)

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

    def test_diagnostics_remove_runtime_install_paths(self):
        runtime_file = Path(sys.prefix) / "lib" / "python" / "threading.py"
        text = (
            'File "%s", line 1\ncommand %s\n'
            % (runtime_file, sys.executable))

        diagnostic = MUTATE._diagnostic(text, self.tmp)

        self.assertNotIn(sys.prefix, diagnostic)
        self.assertNotIn(sys.executable, diagnostic)
        self.assertIn("<python-root>", diagnostic)
        self.assertIn("<python>", diagnostic)

    def test_parallel_kill_must_repeat_in_isolation(self):
        initial = [{
            "outcome": "killed",
            "returncode": 1,
            "duration_ms": 5.0,
            "stdout": "",
            "stderr": "transient lock failure",
            "failing_tests": ["test_unrelated_lock"],
        }]

        def execute(_record):
            return {
                "outcome": "survived",
                "returncode": 0,
                "duration_ms": 3.0,
                "stdout": "",
                "stderr": "",
                "failing_tests": [],
            }

        results, summary = MUTATE.confirm_parallel_candidates(
            initial, [(1, 10)], execute, workers=4)

        self.assertEqual(results[0]["outcome"], "survived")
        self.assertEqual(
            results[0]["execution_mode"], "isolated_confirmation")
        self.assertEqual(
            results[0]["initial_attempt"]["outcome"], "killed")
        self.assertTrue(results[0]["reclassified_parallel_noise"])
        self.assertEqual(summary["candidate_rechecks"], 1)
        self.assertEqual(summary["parallel_noise_reclassified"], 1)

    def test_repeated_parallel_kill_remains_killed(self):
        initial = [{
            "outcome": "killed",
            "returncode": 1,
            "duration_ms": 5.0,
            "stdout": "",
            "stderr": "assertion failed",
            "failing_tests": ["test_behavior"],
        }]

        def execute(_record):
            return dict(initial[0])

        results, summary = MUTATE.confirm_parallel_candidates(
            initial, [(1, 10)], execute, workers=2)

        self.assertEqual(results[0]["outcome"], "killed")
        self.assertFalse(results[0]["reclassified_parallel_noise"])
        self.assertEqual(summary["candidate_rechecks"], 1)
        self.assertEqual(summary["parallel_noise_reclassified"], 0)


if __name__ == "__main__":
    unittest.main()
