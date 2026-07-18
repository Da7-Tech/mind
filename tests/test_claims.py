"""Claims-as-code tests for generated docs and public evidence."""
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "claims_tool", ROOT / "tools" / "claims.py")
CLAIMS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLAIMS)


class ClaimsTests(unittest.TestCase):
    def test_generated_claims_are_current_and_valid(self):
        self.assertEqual(CLAIMS.check(), [])

    def test_public_results_share_clean_artifact_provenance(self):
        facts = CLAIMS.computed_facts()
        expected_hash = hashlib.sha256(
            (ROOT / "mind.py").read_bytes()).hexdigest()

        self.assertEqual(len(facts["public_results"]), 8)
        for relative in facts["public_results"]:
            result = json.loads(
                (ROOT / relative).read_text("utf-8"))
            provenance = result["provenance"]
            self.assertFalse(provenance["dirty"], relative)
            self.assertEqual(
                provenance["mind_sha256"], expected_hash, relative)
            self.assertNotIn("--json-out", result.get("command", ""))

    def test_scoreboard_discloses_scope_and_links_every_report(self):
        english = (ROOT / "README.md").read_text("utf-8")
        arabic = (ROOT / "README.ar.md").read_text("utf-8")
        facts = CLAIMS.computed_facts()

        self.assertIn(
            "BM25 leads both evidence metrics", english)
        self.assertIn(
            "يتفوق خط أساس بي إم خمسة وعشرين", arabic)
        self.assertIn("docs/VERIFICATION.md", english)
        self.assertIn("docs/VERIFICATION.md", arabic)
        for relative in facts["public_results"]:
            name = Path(relative).name
            self.assertIn(name, english)
            self.assertIn(name, arabic)
