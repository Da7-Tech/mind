"""Deterministic modular-source and single-file distribution tests."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import mind as artifact
from src import mind as source

ROOT = Path(__file__).resolve().parent.parent


class TestDistribution(unittest.TestCase):
    def test_manifest_has_named_domain_fragments(self):
        manifest = json.loads(
            (ROOT / "src" / "mind" / "source.json").read_text("utf-8"))
        self.assertEqual(manifest["format"], 1)
        self.assertEqual(manifest["artifact"], "mind.py")
        self.assertEqual(len(manifest["fragments"]), 10)
        self.assertEqual(
            manifest["fragments"][0], "00_prelude.py")
        self.assertEqual(
            manifest["fragments"][-1], "90_cli.py")

    def test_single_file_is_byte_exact_build_output(self):
        result = subprocess.run(
            [sys.executable, "tools/build_single.py", "--check"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_modular_source_and_artifact_have_equivalent_recall(self):
        with tempfile.TemporaryDirectory(
                prefix="mind-source-equivalence-") as temporary:
            root = Path(temporary)
            source_graph = source.Hippocampus(root / "source.json")
            artifact_graph = artifact.Hippocampus(root / "artifact.json")
            facts = (
                "project database is postgres sixteen",
                "deployment target is a container host",
                "formatter is ruff format",
            )
            for fact in facts:
                self.assertEqual(
                    source_graph.remember(fact),
                    artifact_graph.remember(fact),
                )
            source_results = source_graph.recall("which database")[0]
            artifact_results = artifact_graph.recall("which database")[0]
            self.assertEqual(
                [(node_id, node["text"])
                 for node_id, _, node in source_results],
                [(node_id, node["text"])
                 for node_id, _, node in artifact_results],
            )


if __name__ == "__main__":
    unittest.main()
