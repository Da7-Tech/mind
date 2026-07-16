"""Storage lifecycle: segmentation, backup, restore, and compaction."""
import json
import hashlib
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import mind as M


class TestStorageLifecycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mind-storage-")
        self.root = Path(self._tmp.name)
        self.mind = M.Mind(self.root)
        with redirect_stdout(StringIO()):
            self.mind.init()

    def tearDown(self):
        self._tmp.cleanup()

    def _remember(self, text):
        with redirect_stdout(StringIO()):
            self.mind.remember(text)
        return M.Hippocampus._id(text)

    def test_backup_restore_is_verified_and_creates_checkpoint(self):
        original = "backup original database fact"
        original_id = self._remember(original)
        with redirect_stdout(StringIO()):
            name = self.mind.backup("before-change")
            self.mind.remember("later unrelated fact")

        with redirect_stdout(StringIO()):
            plan = self.mind.restore(name, confirm=False)
        self.assertFalse(plan["confirmed"])
        self.assertIn(
            M.Hippocampus._id("later unrelated fact"),
            M.Hippocampus(
                self.root / M.MIND_DIR / M.GRAPH_FILE).nodes,
        )

        with redirect_stdout(StringIO()):
            restored = self.mind.restore(name, confirm=True)

        graph = M.Hippocampus(
            self.root / M.MIND_DIR / M.GRAPH_FILE)
        self.assertIn(original_id, graph.nodes)
        self.assertNotIn(
            M.Hippocampus._id("later unrelated fact"), graph.nodes)
        self.assertTrue(restored["checkpoint"])

    def test_restore_is_exact_and_recovers_after_interruption(self):
        self._remember("restore exact original fact")
        with redirect_stdout(StringIO()):
            name = self.mind.backup("exact")
            self.mind.remember("restore exact later fact")
        extra = self.root / M.MIND_DIR / "later-extra.txt"
        extra.write_text("later extra state", encoding="utf-8")
        storage = self.mind.storage
        original = storage._restore_write_path
        calls = [0]

        def fail_once(source, relative):
            calls[0] += 1
            if calls[0] == 1:
                raise OSError("injected restore interruption")
            return original(source, relative)

        storage._restore_write_path = fail_once
        with self.assertRaises(OSError):
            storage.restore(name, confirm=True)
        self.assertTrue(
            (self.root / M.MIND_DIR / M.RESTORE_OUTBOX_FILE).exists())

        recovered = M.Mind(self.root)
        recovered._ensure()

        backup = (
            self.root / M.MIND_DIR / M.BACKUPS_DIR / name)
        manifest = json.loads(
            (backup / "manifest.json").read_text("utf-8"))
        for entry in manifest["files"]:
            restored = self.root / M.MIND_DIR / entry["path"]
            self.assertEqual(
                hashlib.sha256(restored.read_bytes()).hexdigest(),
                entry["sha256"],
            )
        self.assertFalse(extra.exists())
        self.assertFalse(
            (self.root / M.MIND_DIR / M.RESTORE_OUTBOX_FILE).exists())
        graph = M.Hippocampus(
            self.root / M.MIND_DIR / M.GRAPH_FILE)
        self.assertNotIn(
            M.Hippocampus._id("restore exact later fact"),
            graph.nodes,
        )

    def test_tampered_backup_is_refused(self):
        self._remember("backup digest target")
        with redirect_stdout(StringIO()):
            name = self.mind.backup("tamper")
        backup = (
            self.root / M.MIND_DIR / M.BACKUPS_DIR / name)
        manifest = json.loads(
            (backup / "manifest.json").read_text("utf-8"))
        target = backup / manifest["files"][0]["path"]
        target.write_bytes(target.read_bytes() + b"tampered")

        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.mind.storage.restore(name, confirm=False)

    def test_segmented_journal_reads_as_one_log(self):
        hippo = self.mind.hippo
        first = hippo.remember("journal segment first fact")
        before = hippo.journal_entries().total_count

        segment = self.mind.storage.segment_journal(force=True)
        hippo.remember("journal segment second fact")
        entries = hippo.journal_entries()

        self.assertIsNotNone(segment)
        self.assertTrue(
            (self.root / M.MIND_DIR / segment["segment"]).is_file())
        self.assertGreaterEqual(entries.total_count, before + 2)
        self.assertTrue(any(
            event.get("id") == first for event in entries))
        self.assertTrue(any(
            event.get("op") == "journal-segment" for event in entries))

    def test_compact_dry_run_does_not_change_files(self):
        self._remember("compact dry run fact")
        before = {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and not path.is_symlink()
        }

        result = self.mind.storage.compact(
            dry_run=True, keep_journal_days=90)

        after = {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        self.assertEqual(before, after)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["keep_journal_days"], 90)

    def test_storage_report_surfaces_budget_utilization(self):
        report = self.mind.storage.report()

        for key in ("graph", "signals", "active_archive"):
            self.assertIn("bytes", report[key])
            self.assertIn("budget", report[key])
            self.assertIn("utilization", report[key])
            self.assertIn("estimated_days_to_boundary", report[key])
            self.assertGreater(report[key]["budget"], 0)
        self.assertIn("journal_current", report)
        self.assertIn("journal_segments", report)

    def test_segment_manifest_digest_matches_locked_file(self):
        self._remember("segment digest locked fact")

        result = self.mind.storage.segment_journal(force=True)
        segment = self.root / M.MIND_DIR / result["segment"]

        self.assertEqual(
            hashlib.sha256(segment.read_bytes()).hexdigest(),
            result["sha256"],
        )

    def test_compact_segments_a_wholly_old_current_journal(self):
        journal = self.root / M.MIND_DIR / M.JOURNAL_FILE
        journal.write_text(
            json.dumps({
                "format": 2,
                "ts": "2020-01-01T00:00:00",
                "ts_utc_ns": 1,
                "event_id": "old-event",
                "op": "remember",
                "by": "fixture",
                "id": "old-node",
                "text": "old retained provenance",
            }) + "\n",
            encoding="utf-8",
        )

        result = self.mind.storage.compact(
            dry_run=False, keep_journal_days=30)
        report = self.mind.storage.report()

        self.assertTrue(result["journal_segment"])
        self.assertIsNotNone(result["journal_result"])
        self.assertEqual(report["journal_segments"]["count"], 1)
        self.assertIn(
            "old retained provenance",
            next(
                (self.root / M.MIND_DIR / M.JOURNAL_DIR)
                .glob("*.jsonl")
            ).read_text("utf-8"),
        )
        self.assertIn(
            "journal-segment", journal.read_text("utf-8"))

        output = StringIO()
        with redirect_stdout(output):
            self.mind.status()
        self.assertIn("1 segments", output.getvalue())


if __name__ == "__main__":
    unittest.main()
