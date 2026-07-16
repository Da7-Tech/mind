"""Typed, scoped, trust-labelled, expiring, and slotted memories."""
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import mind as M


class TestTypedMemory(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mind-typed-")
        self.root = Path(self._tmp.name)
        self.mind_dir = self.root / M.MIND_DIR
        self.mind_dir.mkdir()
        (self.mind_dir / M.CORTEX_DIR).mkdir()
        (self.mind_dir / M.DREAMS_DIR).mkdir()
        self.graph = self.mind_dir / M.GRAPH_FILE

    def tearDown(self):
        self._tmp.cleanup()

    def test_metadata_round_trips_and_old_graph_defaults_are_honest(self):
        hippo = M.Hippocampus(self.graph)
        node_id = hippo.remember(
            "deployment decision uses blue green",
            metadata={
                "type": "decision",
                "scope": "project",
                "authority": "maintainer",
                "source_trust": "user",
                "sensitivity": "internal",
                "pinned": True,
                "entity": "deployment",
                "attr": "strategy",
            },
        )

        node = M.Hippocampus(self.graph).nodes[node_id]
        self.assertEqual(node["type"], "decision")
        self.assertEqual(node["scope"], "project")
        self.assertEqual(node["authority"], "maintainer")
        self.assertEqual(node["source_trust"], "user")
        self.assertEqual(node["sensitivity"], "internal")
        self.assertTrue(node["pinned"])
        self.assertEqual(node["entity"], "deployment")
        self.assertEqual(node["attr"], "strategy")

    def test_expired_memory_is_not_recalled(self):
        hippo = M.Hippocampus(self.graph)
        hippo.remember(
            "temporary migration window",
            metadata={"expires_at": "2000-01-01T00:00:00"},
        )

        results, _, _ = hippo.recall("temporary migration window")

        self.assertEqual(results, [])

    def test_pinned_memory_survives_decay(self):
        hippo = M.Hippocampus(self.graph)
        node_id = hippo.remember(
            "pinned operational invariant",
            metadata={"type": "procedural", "pinned": True},
        )
        old = (datetime.now() - timedelta(days=1000)).isoformat()
        node = hippo.nodes[node_id]
        node["created"] = old
        node["last_accessed"] = old
        node["weight"] = 0.01
        node["peak_weight"] = 0.01
        hippo._save()

        self.assertEqual(hippo.decay(), [])
        self.assertIn(node_id, M.Hippocampus(self.graph).nodes)

    def test_slot_collision_is_flagged_even_without_lexical_similarity(self):
        hippo = M.Hippocampus(self.graph)
        first = hippo.remember(
            "postgres sixteen",
            metadata={
                "type": "semantic",
                "entity": "database",
                "attr": "engine",
            },
        )
        second = hippo.remember(
            "cockroach latest",
            metadata={
                "type": "semantic",
                "entity": "database",
                "attr": "engine",
            },
        )
        dreamer = M.Dreamer(
            self.mind_dir, hippo,
            M.Cortex(self.mind_dir / M.CORTEX_DIR))

        _, journal = dreamer.dream()

        self.assertIn("slot conflict database.engine", journal)
        final = M.Hippocampus(self.graph)
        self.assertEqual(
            final.edges[first][second]["relation"],
            "possible-conflict",
        )
        self.assertEqual(
            final.edges[first][second]["conflict_kind"], "slot")
        self.assertEqual(
            final.edges[first][second]["conflict_entity"], "database")
        self.assertEqual(
            final.edges[first][second]["conflict_attr"], "engine")

    def test_sensitive_or_untrusted_memory_never_promotes(self):
        hippo = M.Hippocampus(self.graph)
        for index in range(3):
            hippo.remember(
                "sensitive cluster deployment fact %d" % index,
                metadata={
                    "source_trust": "untrusted",
                    "sensitivity": "sensitive",
                },
            )
        cortex = M.Cortex(self.mind_dir / M.CORTEX_DIR)
        dreamer = M.Dreamer(self.mind_dir, hippo, cortex)

        dreamer.dream()

        self.assertEqual(cortex.files(), [])

    def test_explicit_cli_surface_still_rejects_credentials(self):
        project = M.Mind(self.root)
        with redirect_stdout(StringIO()):
            project.init()

        with self.assertRaisesRegex(ValueError, "secret-or-credential"):
            project.remember(
                "password = explicit-secret-value-123456")


if __name__ == "__main__":
    unittest.main()
