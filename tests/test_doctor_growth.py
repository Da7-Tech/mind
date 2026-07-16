"""Operational doctor, recall receipts, and felt-growth digest."""
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import mind as M


class TestDoctorGrowth(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mind-doctor-")
        self.root = Path(self._tmp.name)
        self.previous_auto = os.environ.get("MIND_AUTO_DREAM")
        os.environ["MIND_AUTO_DREAM"] = "0"
        self.mind = M.Mind(self.root)
        with redirect_stdout(StringIO()):
            self.mind.init()

    def tearDown(self):
        if self.previous_auto is None:
            os.environ.pop("MIND_AUTO_DREAM", None)
        else:
            os.environ["MIND_AUTO_DREAM"] = self.previous_auto
        self._tmp.cleanup()

    def test_doctor_flags_duplicate_guard_blocks(self):
        target = self.root / "AGENTS.md"
        content = target.read_text("utf-8")
        target.write_text(content + "\n" + content, "utf-8")

        result = M.Doctor(
            self.root, self.mind.hippo, self.mind.active).run()

        self.assertFalse(result["ok"])
        self.assertTrue(any(
            finding["code"] == "agent-guard-count"
            for finding in result["findings"]))

    def test_doctor_bench_records_personal_recall_history(self):
        with redirect_stdout(StringIO()):
            self.mind.remember("doctor benchmark database postgres")
            result = self.mind.doctor(run_bench=True)

        history = self.root / M.MIND_DIR / "doctor.jsonl"
        row = json.loads(history.read_text("utf-8").splitlines()[-1])
        self.assertEqual(result["bench"], row)
        self.assertGreater(row["probes"], 0)
        self.assertGreaterEqual(row["recall_at_5"], row["recall_at_1"])

    def test_recall_explain_prints_channel_and_backend_receipts(self):
        with redirect_stdout(StringIO()):
            self.mind.remember("explain receipt database postgres")
        output = StringIO()

        with redirect_stdout(output):
            self.mind.recall(
                "which database has explain receipt", explain=True)

        text = output.getvalue()
        self.assertIn("explain:", text)
        self.assertIn('"direct"', text)
        self.assertIn('"fused"', text)
        self.assertIn("backend receipts", text)

    def test_growth_counts_scripted_activity_and_dream_cycles(self):
        with redirect_stdout(StringIO()):
            self.mind.remember("growth first fact")
            self.mind.remember("growth second fact")
            self.mind.confirm([
                M.Hippocampus._id("growth first fact")])
            self.mind.dream()

        digest = M.Growth(
            self.root / M.MIND_DIR,
            M.Hippocampus(
                self.root / M.MIND_DIR / M.GRAPH_FILE),
            M.Cortex(self.root / M.MIND_DIR / M.CORTEX_DIR),
        ).digest(days=7)

        self.assertEqual(digest["facts_learned"], 2)
        self.assertEqual(digest["facts_confirmed"], 1)
        self.assertGreaterEqual(digest["dream_cycles"], 1)
        self.assertEqual(digest["current_memories"], 2)

    def test_active_memory_exposes_latest_consolidation_receipt(self):
        with redirect_stdout(StringIO()):
            self.mind.dream()

        active = (
            self.root / M.MIND_DIR / M.ACTIVE_FILE
        ).read_text("utf-8")
        self.assertIn("latest consolidation:", active)
        self.assertIn("memories considered", active)


if __name__ == "__main__":
    unittest.main()
