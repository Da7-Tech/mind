"""Deterministic journal merge, UTC event v2, and replay convergence."""
import json
import random
import tempfile
import unittest
from pathlib import Path

import mind as M


def event(op, timestamp, actor="agent", **fields):
    value = {
        "format": 2,
        "ts": timestamp,
        "ts_utc_ns": M.JournalMerger.event_time({"ts": timestamp}),
        "op": op,
        "by": actor,
    }
    value.update(fields)
    value["event_id"] = M.JournalMerger.event_id(value)
    return value


class TestJournalMerge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mind-merge-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _paths(self):
        return (
            self.tmp / "base.jsonl",
            self.tmp / "ours.jsonl",
            self.tmp / "theirs.jsonl",
        )

    def test_merge_is_convergent_and_sums_confirmation_events(self):
        text = "merged service database is postgres"
        node_id = M.Hippocampus._id(text)
        base_event = event(
            "remember", "2026-01-01T00:00:00",
            id=node_id, text=text)
        ours_confirm = event(
            "confirm", "2026-01-02T00:00:00",
            actor="ours", ids=[node_id])
        theirs_confirm = event(
            "confirm", "2026-01-03T00:00:00",
            actor="theirs", ids=[node_id])
        base, ours, theirs = self._paths()
        M.JournalMerger.write(base, [base_event])
        M.JournalMerger.write(ours, [base_event, ours_confirm])
        M.JournalMerger.write(theirs, [base_event, theirs_confirm])
        first_out = self.tmp / "first.jsonl"
        second_out = self.tmp / "second.jsonl"
        first_graph = self.tmp / "first-graph.json"
        second_graph = self.tmp / "second-graph.json"

        M.JournalMerger.merge_files(
            base, ours, theirs, first_out, first_graph)
        M.JournalMerger.merge_files(
            base, theirs, ours, second_out, second_graph)

        self.assertEqual(
            first_out.read_bytes(), second_out.read_bytes())
        self.assertEqual(
            json.loads(first_graph.read_text("utf-8")),
            json.loads(second_graph.read_text("utf-8")),
        )
        graph = M.Hippocampus(first_graph)
        self.assertEqual(graph.nodes[node_id]["access_count"], 2)

    def test_duplicate_suffix_event_is_written_once(self):
        text = "deduplicated merge event"
        node_id = M.Hippocampus._id(text)
        common = event(
            "remember", "2026-01-01T00:00:00",
            id=node_id, text=text)
        shared = event(
            "confirm", "2026-01-02T00:00:00", ids=[node_id])

        merged = M.JournalMerger.merge(
            [common], [common, shared], [common, shared])

        self.assertEqual(len(merged), 2)
        self.assertEqual(
            len({item["event_id"] for item in merged}), 2)

    def test_new_journal_events_have_v2_utc_and_event_identity(self):
        graph = self.tmp / "graph.json"
        hippo = M.Hippocampus(graph)

        hippo.remember("journal v2 event identity")

        raw = json.loads(
            (self.tmp / M.JOURNAL_FILE).read_text(
                "utf-8").splitlines()[0])
        self.assertEqual(raw["format"], 2)
        self.assertIsInstance(raw["ts_utc_ns"], int)
        self.assertGreater(raw["ts_utc_ns"], 0)
        self.assertRegex(raw["event_id"], r"^[0-9a-f]{24}$")

    def test_legacy_naive_event_time_is_timezone_independent(self):
        self.assertEqual(
            M.JournalMerger.event_time({
                "ts": "1970-01-01T00:00:01",
            }),
            1_000_000_000,
        )

    def test_random_branch_interleavings_converge(self):
        for seed in range(20):
            rng = random.Random(seed)
            texts = [
                "merge property fact %d seed %d" % (index, seed)
                for index in range(4)
            ]
            node_ids = [M.Hippocampus._id(text) for text in texts]
            base = [
                event(
                    "remember",
                    "2026-01-01T00:00:%02d" % index,
                    id=node_id,
                    text=text,
                )
                for index, (node_id, text) in enumerate(
                    zip(node_ids, texts))
            ]
            ours = list(base)
            theirs = list(base)
            for index in range(24):
                branch = ours if rng.randrange(2) == 0 else theirs
                branch.append(event(
                    "confirm",
                    "2026-01-02T00:%02d:%02d" % (
                        index // 60, index % 60),
                    actor="branch-%d" % (index % 3),
                    ids=[rng.choice(node_ids)],
                ))
            first = M.JournalMerger.merge(base, ours, theirs)
            second = M.JournalMerger.merge(base, theirs, ours)
            self.assertEqual(first, second)
            first_graph = self.tmp / ("random-%02d-a.json" % seed)
            second_graph = self.tmp / ("random-%02d-b.json" % seed)
            M.JournalMerger.replay(first, first_graph)
            M.JournalMerger.replay(second, second_graph)
            self.assertEqual(
                json.loads(first_graph.read_text("utf-8")),
                json.loads(second_graph.read_text("utf-8")),
            )


if __name__ == "__main__":
    unittest.main()
