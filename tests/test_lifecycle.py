"""Privacy lifecycle tests across every managed storage surface."""
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import mind as M


class TestPrivacyLifecycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mind-lifecycle-")
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

    def _seed_all_text_stores(self, text):
        mind_dir = self.root / M.MIND_DIR
        (mind_dir / "archive.md").write_text(
            "# archive\n- %s\n" % text, "utf-8")
        (mind_dir / M.DREAMS_DIR / "2026-07-16.md").write_text(
            "# dream\n%s\n" % text, "utf-8")
        (mind_dir / M.CORTEX_DIR / "manual.md").write_text(
            "# cortex\n%s\n" % text, "utf-8")
        (mind_dir / M.SIGNALS_FILE).write_text(
            '{"content": %r}\n' % text, "utf-8")
        (mind_dir / M.PENDING_FILE).write_text(
            '[{"id":"pending","text":%r}]' % text, "utf-8")

    def _assert_payload_absent(self, payload):
        needle = payload.encode("utf-8")
        found = []
        for path in self.root.rglob("*"):
            if (not path.is_file() or path.is_symlink()
                    or path.name == M.RUNTIME_FILE):
                continue
            if needle in path.read_bytes():
                found.append(str(path.relative_to(self.root)))
        self.assertEqual(found, [])

    def test_forget_hides_memory_without_destroying_provenance(self):
        text = "forget lifecycle project fact"
        node_id = self._remember(text)

        with redirect_stdout(StringIO()):
            self.mind.forget(node_id, "no longer relevant")

        hippo = M.Hippocampus(
            self.root / M.MIND_DIR / M.GRAPH_FILE)
        results, _, _ = hippo.recall("forget lifecycle project fact")
        self.assertEqual(results, [])
        self.assertIn(node_id, hippo.nodes)
        self.assertTrue(hippo.nodes[node_id]["forgotten_at"])
        self.assertTrue(any(
            event.get("op") == "forget"
            for event in hippo.journal_entries(node_id)))

    def test_unlink_removes_both_directions(self):
        left = "unlink first endpoint"
        right = "unlink second endpoint"
        with redirect_stdout(StringIO()):
            self.mind.link(left, right, "depends-on")

        with redirect_stdout(StringIO()):
            self.mind.unlink(
                M.Hippocampus._id(left),
                M.Hippocampus._id(right))

        hippo = M.Hippocampus(
            self.root / M.MIND_DIR / M.GRAPH_FILE)
        self.assertNotIn(
            M.Hippocampus._id(right),
            hippo.edges.get(M.Hippocampus._id(left), {}))
        self.assertNotIn(
            M.Hippocampus._id(left),
            hippo.edges.get(M.Hippocampus._id(right), {}))

    def test_redact_replaces_payload_across_all_managed_stores(self):
        text = "redact this private payload value"
        node_id = self._remember(text)
        self._seed_all_text_stores(text)

        with redirect_stdout(StringIO()):
            self.mind.redact(node_id, "privacy correction")

        hippo = M.Hippocampus(
            self.root / M.MIND_DIR / M.GRAPH_FILE)
        self.assertIn("[REDACTED sha256:", hippo.nodes[node_id]["text"])
        self._assert_payload_absent(text)
        self.assertFalse(
            (self.root / M.MIND_DIR /
             M.LIFECYCLE_OUTBOX_FILE).exists())

    def test_purge_dry_run_then_confirm_removes_payload_and_node_id(self):
        text = "purge this accidental credential payload"
        node_id = self._remember(text)
        self._seed_all_text_stores(text)

        with redirect_stdout(StringIO()):
            inventory = self.mind.purge(node_id, confirm=False)

        self.assertGreater(inventory["occurrences"], 0)
        self.assertIn(
            node_id,
            M.Hippocampus(
                self.root / M.MIND_DIR / M.GRAPH_FILE).nodes)

        with redirect_stdout(StringIO()):
            self.mind.purge(node_id, confirm=True)

        hippo = M.Hippocampus(
            self.root / M.MIND_DIR / M.GRAPH_FILE)
        self.assertNotIn(node_id, hippo.nodes)
        self._assert_payload_absent(text)
        self._assert_payload_absent(node_id)

    def test_purge_rewrites_and_revalidates_existing_backups(self):
        text = "purge payload already captured in a backup"
        node_id = self._remember(text)
        with redirect_stdout(StringIO()):
            backup_name = self.mind.backup("before-privacy-remediation")
            self.mind.purge(node_id, confirm=True)

        backup_root = (
            self.root / M.MIND_DIR / M.BACKUPS_DIR / backup_name)
        self.assertNotIn(
            text.encode("utf-8"),
            b"".join(
                path.read_bytes()
                for path in backup_root.rglob("*")
                if path.is_file() and not path.is_symlink()
            ),
        )
        source, manifest = self.mind.storage._load_backup(
            backup_name)
        self.assertEqual(source.resolve(), backup_root.resolve())
        self.assertTrue(manifest["privacy_rewritten"])

    def test_interrupted_redaction_recovers_on_next_open(self):
        text = "recover interrupted redaction payload"
        node_id = self._remember(text)
        self._seed_all_text_stores(text)
        original = self.mind.lifecycle._rewrite_path
        failed = [False]

        def fail_once(*args, **kwargs):
            if not failed[0]:
                failed[0] = True
                raise OSError("injected lifecycle interruption")
            return original(*args, **kwargs)

        self.mind.lifecycle._rewrite_path = fail_once
        with self.assertRaises(OSError):
            self.mind.lifecycle.begin(
                "redact", node_id, reason="recovery test")
        self.assertTrue(
            (self.root / M.MIND_DIR /
             M.LIFECYCLE_OUTBOX_FILE).exists())

        recovered = M.Mind(self.root)
        with redirect_stdout(StringIO()):
            recovered._ensure()

        self.assertFalse(
            (self.root / M.MIND_DIR /
             M.LIFECYCLE_OUTBOX_FILE).exists())
        self._assert_payload_absent(text)


if __name__ == "__main__":
    unittest.main()
