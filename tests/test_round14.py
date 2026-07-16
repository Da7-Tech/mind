"""Regression tests for the consolidated 2026-07-16 audit."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import mind as M
from mind import Active, Cortex, Hippocampus, Mind


class ConsolidatedAuditTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mind-round14-")
        self.root = Path(self._tmp.name)
        self.mind_dir = self.root / M.MIND_DIR
        self.mind_dir.mkdir()
        (self.mind_dir / M.CORTEX_DIR).mkdir()
        (self.mind_dir / M.DREAMS_DIR).mkdir()
        self.graph = self.mind_dir / M.GRAPH_FILE

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _weaken(hippo, node_id):
        old = (datetime.now() - timedelta(days=200)).isoformat()
        node = hippo.nodes[node_id]
        node["last_accessed"] = old
        node["created"] = old
        node["weight"] = 0.05
        node["peak_weight"] = 0.05
        node["access_count"] = 0
        hippo._save()

    def test_oversized_archive_never_blocks_prune_recovery_or_writes(self):
        hippo = Hippocampus(self.graph)
        text = "oversized archive recovery target"
        node_id = hippo.remember(text)
        self._weaken(hippo, node_id)
        original_recover = hippo._recover_prune_outbox
        calls = [0]

        def crash_after_graph_commit():
            calls[0] += 1
            if calls[0] == 1:
                return original_recover()
            raise OSError("injected crash after graph commit")

        hippo._recover_prune_outbox = crash_after_graph_commit
        with self.assertRaises(OSError):
            hippo.decay()
        self.assertTrue((self.mind_dir / M.PRUNE_OUTBOX_FILE).exists())

        archive = self.mind_dir / "archive.md"
        with archive.open("wb") as handle:
            handle.write(b"# old archive\n")
            handle.truncate(100_000_000)

        trigger = Hippocampus(self.graph)
        trigger.remember("write remains available during prune recovery")

        self.assertFalse((self.mind_dir / M.PRUNE_OUTBOX_FILE).exists())
        archives = list(self.mind_dir.glob("archive*.md"))
        self.assertTrue(any(
            path.name != "archive.md"
            and path.stat().st_size >= 100_000_000
            for path in archives))
        self.assertIn(
            text,
            (self.mind_dir / "archive.md").read_text(
                "utf-8", errors="ignore"))
        self.assertIn(
            Hippocampus._id("write remains available during prune recovery"),
            Hippocampus(self.graph).nodes,
        )

    def test_crlf_export_is_idempotent_and_preserves_user_bytes(self):
        hippo = Hippocampus(self.graph)
        hippo.remember("crlf export fact")
        cortex = Cortex(self.mind_dir / M.CORTEX_DIR)
        active = Active(self.mind_dir, hippo, cortex)
        active.generate(self.root)
        target = self.root / "AGENTS.md"
        target.write_bytes(b"## User policy\nkeep this byte-for-byte\n")
        active.export_to_agents(self.root)
        target.write_bytes(
            b"\r\n".join(target.read_bytes().splitlines()) + b"\r\n"
        )
        user = b"## User policy\r\nkeep this byte-for-byte\r\n"

        active.export_to_agents(self.root)
        first = target.read_bytes()
        active.export_to_agents(self.root)
        second = target.read_bytes()

        self.assertEqual(first, second)
        self.assertEqual(first.count(Active.BEGIN.encode("utf-8")), 1)
        self.assertEqual(first.count(user), 1)

    def test_cortex_repromotion_preserves_multiline_and_manual_content(self):
        cortex = Cortex(self.mind_dir / M.CORTEX_DIR)
        relative = cortex.promote(
            "deployment policy",
            "- deployment uses blue green\n"
            "  with a ten minute observation window",
        )
        target = self.mind_dir / relative
        manual = "\n## Human rationale\nKeep rollback capacity warm.\n"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(manual)

        cortex.promote(
            "deployment policy",
            "- deployment requires a health check",
        )
        content = target.read_text("utf-8")

        self.assertIn("with a ten minute observation window", content)
        self.assertIn(manual.strip(), content)
        self.assertIn("- deployment requires a health check", content)

    def test_oversized_signals_self_heal_and_auto_dream_resumes(self):
        shutil.rmtree(self.mind_dir)
        mind = Mind(self.root)
        mind.init()
        signals = self.mind_dir / M.SIGNALS_FILE
        signals.write_bytes(
            (b'{"kind":"remember","content":"x","ts":"old"}\n')
            * 130_000
        )
        self.assertGreater(signals.stat().st_size, 5_000_000)

        mind.remember("auto dream survives oversized signals")

        self.assertLess(signals.stat().st_size, 5_000_000)
        journals = list((self.mind_dir / M.DREAMS_DIR).glob("*.md"))
        self.assertTrue(journals)
        events = Hippocampus(self.graph).journal_entries()
        self.assertTrue(any(
            event.get("op") == "signals-reset" for event in events
        ))
        scheduler = json.loads(
            (self.mind_dir / M.SCHEDULER_FILE).read_text("utf-8")
        )
        self.assertEqual(scheduler["pending"], 0)
        self.assertGreater(scheduler["last_dream_ns"], 0)

    def test_failed_transaction_cannot_clobber_concurrent_commit(self):
        initial = Hippocampus(self.graph)
        target = initial.remember("transaction recovery target")
        stale = Hippocampus(self.graph)
        original_commit = stale._commit_current

        def fail_commit():
            raise OSError("injected commit failure")

        stale._commit_current = fail_commit
        with self.assertRaises(OSError):
            stale.remember("failed transaction local node")
        stale._commit_current = original_commit

        concurrent = Hippocampus(self.graph)
        concurrent.bump([target])
        stale.remember("next operation after failed transaction")

        final = Hippocampus(self.graph)
        self.assertEqual(final.nodes[target]["access_count"], 1)
        self.assertIn(
            Hippocampus._id("next operation after failed transaction"),
            final.nodes,
        )

    def test_write_reloads_once_without_legacy_mutation_trackers(self):
        hippo = Hippocampus(self.graph)
        hippo.remember("transaction reload seed")
        calls = [0]
        original = hippo._load

        def counted_load():
            calls[0] += 1
            return original()

        hippo._load = counted_load
        hippo.remember("transaction reload second fact")

        self.assertEqual(calls[0], 1)
        for name in (
                "_decayed", "_bumped", "_conf_raised", "_dirty",
                "_pruned_edges", "_edge_updates", "_edge_bumps",
                "_last_edge_pruned"):
            self.assertFalse(hasattr(hippo, name), name)

    def test_recall_uses_one_batch_process(self):
        counter = self.root / "embed-calls.txt"
        script = self.root / "batch-embedder.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "counter = pathlib.Path(sys.argv[1])\n"
            "with counter.open('a', encoding='utf-8') as handle:\n"
            "    handle.write('call\\n')\n"
            "request = json.load(sys.stdin)\n"
            "vectors = []\n"
            "for text in request['texts']:\n"
            "    vectors.append([1.0, 0.0] if 'alpha' in text else [0.0, 1.0])\n"
            "json.dump({'protocol': 'mind-embed-v1', 'model': 'fixture',\n"
            "           'vectors': vectors}, sys.stdout)\n",
            encoding="utf-8",
        )
        old = os.environ.get("MIND_EMBED_CMD")
        os.environ["MIND_EMBED_CMD"] = "%s %s %s" % (
            sys.executable, script, counter)
        try:
            hippo = Hippocampus(self.graph)
            for index in range(8):
                hippo.remember(
                    "alpha candidate %d with shared retrieval terms" % index
                )
            hippo.recall("alpha shared retrieval terms")
        finally:
            if old is None:
                os.environ.pop("MIND_EMBED_CMD", None)
            else:
                os.environ["MIND_EMBED_CMD"] = old

        self.assertEqual(counter.read_text("utf-8").splitlines(), ["call"])
        self.assertEqual(hippo.reranker.last_report["calls"], 1)
        self.assertFalse(hippo.reranker.last_report["fallback"])

    def test_partial_batch_failure_falls_back_for_the_whole_ranking(self):
        script = self.root / "partial-batch.py"
        script.write_text(
            "import json, sys\n"
            "request = json.load(sys.stdin)\n"
            "json.dump({'protocol': 'mind-embed-v1',\n"
            "           'vectors': [[1.0, 0.0]] * (len(request['texts']) - 1)},\n"
            "          sys.stdout)\n",
            encoding="utf-8",
        )
        fallback = M.HashEmbed(dim=32)
        embedder = M.CommandEmbed(
            cmd="%s %s" % (sys.executable, script),
            fallback=fallback,
            project_root=self.root,
        )
        query = "shared query"
        candidates = ["candidate alpha", "candidate beta"]

        scores = embedder.similarities(query, candidates)

        self.assertEqual(
            scores,
            [fallback.similarity(query, candidate)
             for candidate in candidates],
        )
        self.assertTrue(embedder.last_report["fallback"])
        self.assertEqual(
            embedder.last_report["reason"], "partial batch response")

    def test_hanging_batch_backend_obeys_total_deadline(self):
        script = self.root / "hanging-batch.py"
        script.write_text(
            "import time\n"
            "time.sleep(5)\n",
            encoding="utf-8",
        )
        embedder = M.CommandEmbed(
            cmd="%s %s" % (sys.executable, script),
            fallback=M.HashEmbed(dim=32),
            budget=0.2,
            project_root=self.root,
        )
        started = time.monotonic()

        scores = embedder.similarities(
            "deadline query", ["first candidate", "second candidate"])

        elapsed = time.monotonic() - started
        self.assertEqual(len(scores), 2)
        self.assertLess(elapsed, 1.5)
        self.assertTrue(embedder.last_report["fallback"])
        self.assertEqual(
            embedder.last_report["reason"], "total deadline exceeded")

    def test_directional_relation_has_truthful_reverse_label(self):
        hippo = Hippocampus(self.graph)
        left = "api gateway service"
        right = "authentication core service"

        hippo.link(left, right, "depends-on")

        left_id = hippo._id(left)
        right_id = hippo._id(right)
        self.assertEqual(
            hippo.edges[left_id][right_id]["relation"], "depends-on")
        self.assertEqual(
            hippo.edges[right_id][left_id]["relation"], "required-by")
        self.assertTrue(hippo.edges[left_id][right_id]["directed"])
        self.assertTrue(hippo.edges[right_id][left_id]["directed"])

    def test_content_ids_explicitly_mark_md5_as_non_security(self):
        original = M.hashlib.md5
        calls = []

        def fips_md5(payload, **kwargs):
            calls.append(kwargs)
            if kwargs.get("usedforsecurity") is not False:
                raise ValueError("FIPS mode")
            return original(payload)

        M.hashlib.md5 = fips_md5
        try:
            node_id = Hippocampus._id("fips compatible content id")
            Cortex(self.mind_dir / M.CORTEX_DIR).promote(
                "fips topic", "- fips compatible cortex")
        finally:
            M.hashlib.md5 = original

        self.assertRegex(node_id, r"^[0-9a-f]{12}$")
        self.assertTrue(calls)
        self.assertTrue(all(
            call.get("usedforsecurity") is False for call in calls))

    def test_windows_invocation_uses_stock_python_launcher(self):
        old_argv = list(sys.argv)
        script = self.root / "mind.py"
        script.write_text("print('fixture')\n", encoding="utf-8")
        sys.argv[:] = [str(script)]
        try:
            self.assertEqual(
                M._invocation(self.root, platform="nt"),
                "py -3 mind.py",
            )
        finally:
            sys.argv[:] = old_argv

    @unittest.skipUnless(
        os.name == "nt", "Windows field path runs in the Windows CI cells")
    def test_windows_crlf_exported_invocation_executes_verbatim(self):
        source = Path(M.__file__).read_bytes()
        script = self.root / "mind.py"
        script.write_bytes(
            b"\r\n".join(source.splitlines()) + b"\r\n")
        initialized = subprocess.run(
            ["py", "-3", "mind.py", "init"],
            cwd=self.root, capture_output=True, text=True)
        self.assertEqual(
            initialized.returncode, 0,
            initialized.stdout + initialized.stderr)
        agent = (self.root / "AGENTS.md").read_text("utf-8")
        self.assertIn("`py -3 mind.py recall", agent)
        status = subprocess.run(
            ["py", "-3", "mind.py", "status"],
            cwd=self.root, capture_output=True, text=True)
        self.assertEqual(
            status.returncode, 0, status.stdout + status.stderr)
        self.assertIn("mind memory health", status.stdout)

    def test_recall_end_of_options_accepts_dash_leading_query(self):
        mind = Mind(self.root)
        with redirect_stdout(StringIO()):
            mind.init()
            mind.remember("--dry-run is documented as a preview flag")
        output = StringIO()
        error = StringIO()
        previous = Path.cwd()
        os.chdir(self.root)
        try:
            with redirect_stdout(output), redirect_stderr(error):
                code = M.main([
                    "recall", "--", "--dry-run is documented where"])
        finally:
            os.chdir(previous)

        self.assertEqual(code, 0, error.getvalue())
        self.assertIn("--dry-run", output.getvalue())

    def test_conflict_first_seen_timestamp_survives_repeated_dreams(self):
        hippo = Hippocampus(self.graph)
        first = "payment provider stripe charges two percent fees"
        second = "payment provider paypal charges three percent fees"
        hippo.remember(first)
        hippo.remember(second)
        dreamer = M.Dreamer(
            self.mind_dir, hippo,
            Cortex(self.mind_dir / M.CORTEX_DIR))
        dreamer.dream()
        first_id = hippo._id(first)
        second_id = hippo._id(second)
        created = Hippocampus(self.graph).edges[
            first_id][second_id]["created"]

        dreamer.dream()

        final = Hippocampus(self.graph)
        self.assertEqual(
            final.edges[first_id][second_id]["created"], created)
        self.assertEqual(
            final.edges[second_id][first_id]["created"], created)

    def test_init_sweeps_only_old_regular_tmp_files(self):
        old = self.mind_dir / "graph.json.abandoned.tmp"
        fresh = self.mind_dir / "graph.json.fresh.tmp"
        old.write_text("old", "utf-8")
        fresh.write_text("fresh", "utf-8")
        old_time = time.time() - 2 * 24 * 3600
        os.utime(old, (old_time, old_time))

        with redirect_stdout(StringIO()):
            Mind(self.root).init()

        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_verbose_version_distinguishes_source_identity(self):
        output = StringIO()

        with redirect_stdout(output):
            code = M.main(["--version", "--verbose"])

        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn(M.__version__, text)
        self.assertRegex(text, r"sha256=[0-9a-f]{64}")

    def test_bulk_ingest_uses_one_graph_commit_and_batched_logs(self):
        hippo = Hippocampus(self.graph)
        graph_writes = []
        appends = []
        original_atomic = M._atomic_write
        original_append = M._append_regular

        def counted_atomic(path, data, **kwargs):
            if Path(path) == self.graph:
                graph_writes.append(Path(path))
            return original_atomic(path, data, **kwargs)

        def counted_append(path, payload, boundary, **kwargs):
            appends.append(Path(path).name)
            return original_append(
                path, payload, boundary=boundary, **kwargs)

        M._atomic_write = counted_atomic
        M._append_regular = counted_append
        try:
            node_ids = hippo.remember_many([
                {"text": "batch fact %03d" % index}
                for index in range(100)
            ])
        finally:
            M._atomic_write = original_atomic
            M._append_regular = original_append

        self.assertEqual(len(node_ids), 100)
        self.assertEqual(len(graph_writes), 1)
        self.assertEqual(appends.count(M.JOURNAL_FILE), 1)
        self.assertEqual(appends.count(M.SIGNALS_FILE), 1)
        self.assertEqual(
            sum(event.get("op") == "remember"
                for event in hippo.journal_entries()),
            100,
        )

    def test_persistent_embed_server_starts_once_and_reuses_handshake(self):
        counter = self.root / "server-starts.txt"
        server = (
            Path(M.__file__).resolve().parent
            / "contrib" / "concept_embed_server.py")
        previous = os.environ.get("MIND_EMBED_SERVER")
        os.environ["MIND_EMBED_SERVER"] = "%s %s --counter %s" % (
            sys.executable, server, counter)
        try:
            embedder = M.CommandEmbed(
                fallback=M.HashEmbed(dim=32),
                project_root=self.root,
            )
            first = embedder.similarities(
                "database query", [
                    "postgres database", "frontend react"])
            second = embedder.similarities(
                "cache query", ["redis cache", "postgres database"])
            report = dict(embedder.last_report)
            embedder.close()
        finally:
            if previous is None:
                os.environ.pop("MIND_EMBED_SERVER", None)
            else:
                os.environ["MIND_EMBED_SERVER"] = previous

        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertEqual(counter.read_text("utf-8"), "1")
        self.assertEqual(report["backend"], "server")
        self.assertIn("stdlib-concept-hash@2", report["model"])


if __name__ == "__main__":
    unittest.main()
