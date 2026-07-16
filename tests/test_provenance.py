import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench.provenance import reproducible_command, repo_provenance


class ProvenanceTests(unittest.TestCase):
    def test_command_strips_output_path(self):
        command = reproducible_command([
            str(ROOT / "bench" / "bulk.py"),
            "--records", "10",
            "--json-out", "/private-output/report.json",
        ])
        self.assertIn("bench/bulk.py --records 10", command)
        self.assertNotIn("json-out", command)
        self.assertNotIn("/Users/", command)

    def test_source_labels_are_repository_relative(self):
        report = repo_provenance(("bench/bulk.py",))
        self.assertEqual(
            set(report["sources"]),
            {"bench/bulk.py"},
        )
        self.assertEqual(len(report["sources"]["bench/bulk.py"]), 64)

    def test_public_benchmark_report_has_no_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bench" / "bulk.py"),
                    "--records", "10",
                    "--serial-sample", "2",
                    "--serial-repetitions", "1",
                    "--json-out", str(output),
                ],
                cwd=str(ROOT),
                check=True,
                capture_output=True,
                text=True,
            )
            report = json.loads(output.read_text("utf-8"))
        self.assertIn("provenance", report)
        self.assertNotIn(str(output), json.dumps(report))


if __name__ == "__main__":
    unittest.main()
