"""mind test suite — stdlib unittest only (zero dependencies, like the tool).

Run:  python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mind as M                                    # noqa: E402
from mind import (Hippocampus, Cortex, Dreamer, Active, Mind,  # noqa: E402
                  RelatedTerms, HashEmbed, stem, _atomic_write)


class TmpDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mind-test-"))
        self.mind_dir = self.tmp / ".mind"
        self.mind_dir.mkdir()
        (self.mind_dir / "cortex").mkdir()
        (self.mind_dir / "dreams").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hippo(self):
        return Hippocampus(self.mind_dir / "graph.json")


# ────────────────────────────── stemmer ──────────────────────────────
class TestStem(unittest.TestCase):
    def test_english_plural(self):
        self.assertEqual(stem("databases"), "database")
        self.assertEqual(stem("boxes"), "box")
        self.assertEqual(stem("studies"), "study")

    def test_english_ing(self):
        self.assertEqual(stem("testing"), "test")

    def test_arabic_definite_article(self):
        self.assertEqual(stem("المشروع"), "مشروع")

    def test_arabic_broken_plural_unifies_with_singular(self):
        self.assertEqual(stem("قواعد"), stem("قاعدة"))
        self.assertEqual(stem("مشاريع"), stem("مشروع"))

    def test_short_words_untouched(self):
        self.assertEqual(stem("db"), "db")


# ─────────────────────────── related terms ───────────────────────────
class TestRelatedTerms(unittest.TestCase):
    CORPUS = [
        "the project uses sqlite database for storage",
        "sqlite database chosen over postgres",
        "frontend uses react and typescript",
        "react frontend talks to the flask backend",
        "flask backend exposes a json api",
    ]

    def test_cooccurring_terms_are_related(self):
        rt = RelatedTerms(self.CORPUS)
        related = [t for t, _ in rt.related("sqlite", top_k=5)]
        self.assertIn("database", related)

    def test_two_hop_bridge(self):
        # react ~ frontend ~ typescript: typescript reachable from react
        rt = RelatedTerms(self.CORPUS)
        related = [t for t, _ in rt.related("react", top_k=8)]
        self.assertIn("typescript", related)

    def test_unknown_word_falls_back_to_fuzzy(self):
        rt = RelatedTerms(self.CORPUS)
        related = [t for t, _ in rt.related("sqlitee", top_k=3)]
        self.assertIn("sqlite", related)

    def test_empty_query(self):
        rt = RelatedTerms(self.CORPUS)
        self.assertEqual(rt.related(""), [])


# ───────────────────────────── hash embed ─────────────────────────────
class TestHashEmbed(unittest.TestCase):
    def test_identical_texts_similarity_one(self):
        e = HashEmbed()
        self.assertAlmostEqual(e.similarity("hello world", "hello world"), 1.0, places=5)

    def test_related_texts_more_similar_than_unrelated(self):
        e = HashEmbed()
        rel = e.similarity("sqlite database storage", "the database is sqlite")
        unrel = e.similarity("sqlite database storage", "purple monkey dishwasher")
        self.assertGreater(rel, unrel)

    def test_arabic_similarity(self):
        e = HashEmbed()
        rel = e.similarity("المشروع يستخدم قاعدة بيانات", "قاعدة البيانات للمشروع")
        unrel = e.similarity("المشروع يستخدم قاعدة بيانات", "الطقس اليوم جميل")
        self.assertGreater(rel, unrel)


# ─────────────────────────── hippocampus ───────────────────────────
class TestRememberRecall(TmpDirTest):
    def test_remember_and_direct_recall(self):
        h = self.hippo()
        h.remember("the project uses sqlite for storage")
        results, latency, kinds = h.recall("sqlite")
        self.assertEqual(len(results), 1)
        self.assertIn("sqlite", results[0][2]["text"])

    def test_empty_text_rejected(self):
        h = self.hippo()
        with self.assertRaises(ValueError):
            h.remember("   ")

    def test_duplicate_remember_reinforces_not_duplicates(self):
        h = self.hippo()
        h.remember("user prefers dark mode")
        h.remember("user prefers dark mode")
        self.assertEqual(len(h.nodes), 1)
        node = next(iter(h.nodes.values()))
        self.assertEqual(node["access_count"], 1)

    def test_unknown_unknown_via_related_terms(self):
        # ask "database" while only "sqlite" was stored alongside context
        h = self.hippo()
        h.remember("we store everything in sqlite, our database of choice")
        h.remember("the frontend is react")
        results, _, _ = h.recall("which database do we use")
        self.assertTrue(results)
        self.assertIn("sqlite", results[0][2]["text"])

    def test_multi_hop_recall_through_links(self):
        h = self.hippo()
        h.remember("khaled works on the souq app")
        h.remember("souq app uses postgres")
        h.link("khaled works on the souq app", "souq app uses postgres", "uses")
        results, _, kinds = h.recall("khaled")
        texts = [r[2]["text"] for r in results]
        self.assertTrue(any("postgres" in t for t in texts),
                        "linked node should surface via spreading activation")

    def test_recall_is_read_only(self):
        h = self.hippo()
        h.remember("some fact about python")
        before = json.dumps(h.nodes, sort_keys=True)
        h.recall("python")
        after = json.dumps(h.nodes, sort_keys=True)
        self.assertEqual(before, after, "recall must not mutate the graph")

    def test_bump_reinforces(self):
        h = self.hippo()
        nid = h.remember("important fact about deployment")
        w0 = h.nodes[nid]["weight"]
        h.bump([nid])
        self.assertGreater(h.nodes[nid]["weight"], w0 - 1e-9)
        self.assertEqual(h.nodes[nid]["access_count"], 1)

    def test_pattern_completion_fuzzy_recall(self):
        h = self.hippo()
        h.remember("deployment target is kubernetes on hetzner")
        # misspelled cue with no exact key overlap
        results, _, _ = h.recall("kubernets deploymnt")
        self.assertTrue(results, "fuzzy cue should still reactivate the memory")

    def test_pattern_separation_diversifies_topk(self):
        h = self.hippo()
        h.remember("api rate limit is 100 requests per minute")
        h.remember("api rate limit is 100 requests each minute")  # near-dup
        h.remember("api auth uses bearer tokens")
        results, _, _ = h.recall("api")
        texts = [r[2]["text"] for r in results]
        # both near-dups must not crowd out the distinct auth memory
        self.assertTrue(any("bearer" in t for t in texts))

    def test_arabic_recall(self):
        h = self.hippo()
        h.remember("اسم المستخدم خالد وهو مطور من الرياض")
        results, _, _ = h.recall("ما اسمي")
        self.assertTrue(results)
        self.assertIn("خالد", results[0][2]["text"])

    def test_cross_language_normalization(self):
        h = self.hippo()
        h.remember("المشروع يستخدم بايثون")
        results, _, _ = h.recall("python")
        self.assertTrue(results)

    def test_content_free_memory_does_not_pollute_identity(self):
        """Regression: an emoji-only memory must not outrank the real name
        on identity queries (identity fallback is query-side only)."""
        h = self.hippo()
        h.remember("🚀🚀🚀")
        h.remember("my name is khaled and I live in riyadh")
        results, _, _ = h.recall("what is my name")
        self.assertTrue(results)
        self.assertIn("khaled", results[0][2]["text"])

    def test_concurrent_processes_do_not_lose_writes(self):
        """Regression: two hippocampi loaded from the same file, both
        writing — the second save must not erase the first's node."""
        h1 = self.hippo()
        h2 = Hippocampus(self.mind_dir / "graph.json")
        h1.remember("fact from process one")
        h2.remember("fact from process two")     # h2 loaded before h1's write
        h3 = Hippocampus(self.mind_dir / "graph.json")
        texts = [n["text"] for n in h3.nodes.values()]
        self.assertIn("fact from process one", texts)
        self.assertIn("fact from process two", texts)

    def test_ansi_escapes_stripped_on_remember(self):
        h = self.hippo()
        nid = h.remember("colored \x1b[31mred\x1b[0m text")
        self.assertNotIn("\x1b", h.nodes[nid]["text"])


class TestCorrect(TmpDirTest):
    def test_correct_rewrites_and_keeps_history(self):
        h = self.hippo()
        h.remember("the database is mysql")
        old = h.correct("database mysql", "the database is postgres")
        self.assertEqual(old, "the database is mysql")
        self.assertEqual(len(h.nodes), 1)
        node = next(iter(h.nodes.values()))
        self.assertIn("postgres", node["text"])
        self.assertEqual(node["history"][0]["text"], "the database is mysql")
        self.assertLess(node["confidence"], 1.0)

    def test_correct_moves_edges(self):
        h = self.hippo()
        h.remember("the database is mysql")
        h.remember("backend is flask")
        h.link("the database is mysql", "backend is flask")
        h.correct("database mysql", "the database is postgres")
        new_id = h._id("the database is postgres")
        self.assertIn(new_id, h.edges)
        self.assertTrue(h.edges[new_id], "edges must follow the corrected node")

    def test_correct_on_empty_graph(self):
        h = self.hippo()
        self.assertIsNone(h.correct("anything", "new"))

    def test_correct_refuses_one_word_coincidence(self):
        """Regression: a hint sharing a single token with an unrelated
        memory must not rewrite it (destructive-op gate)."""
        h = self.hippo()
        h.remember("fix the quote handling in the parser")
        result = h.correct("flooble grommit handling", "corrected nonsense")
        self.assertIsNone(result)
        node = next(iter(h.nodes.values()))
        self.assertIn("quote handling", node["text"])

    def test_corrected_memory_wins_recall(self):
        h = self.hippo()
        h.remember("the database is mysql")
        h.correct("database mysql", "the database is postgres")
        results, _, _ = h.recall("which database")
        self.assertIn("postgres", results[0][2]["text"])


class TestDecay(TmpDirTest):
    def _age(self, h, nid, days):
        h.nodes[nid]["last_accessed"] = (
            datetime.now() - timedelta(days=days)).isoformat()

    def test_unused_memory_decays_and_prunes(self):
        h = self.hippo()
        nid = h.remember("trivial one-off note")
        self._age(h, nid, 30)
        pruned = h.decay()
        self.assertIn("trivial one-off note", pruned)
        self.assertNotIn(nid, h.nodes)

    def test_reinforced_memory_survives(self):
        h = self.hippo()
        nid = h.remember("critical architecture decision")
        h.bump([nid]); h.bump([nid]); h.bump([nid])
        self._age(h, nid, 30)
        pruned = h.decay()
        self.assertNotIn("critical architecture decision", pruned)
        self.assertIn(nid, h.nodes)

    def test_fresh_memory_untouched(self):
        h = self.hippo()
        nid = h.remember("fresh fact")
        h.decay()
        self.assertIn(nid, h.nodes)
        self.assertGreater(h.nodes[nid]["weight"], 0.9)

    def test_decay_dry_run_does_not_delete(self):
        h = self.hippo()
        nid = h.remember("trivial one-off note")
        self._age(h, nid, 30)
        pruned = h.decay(dry_run=True)
        self.assertTrue(pruned)
        self.assertIn(nid, h.nodes, "dry run must not delete")


class TestPersistence(TmpDirTest):
    def test_graph_survives_reload(self):
        h = self.hippo()
        h.remember("persistent fact")
        h2 = Hippocampus(self.mind_dir / "graph.json")
        self.assertEqual(len(h2.nodes), 1)

    def test_corrupt_graph_quarantined_not_erased(self):
        gpath = self.mind_dir / "graph.json"
        gpath.write_text("{not json", encoding="utf-8")
        h = Hippocampus(gpath)
        self.assertEqual(h.nodes, {})
        corrupt = list(self.mind_dir.glob("graph.json.corrupt-*"))
        self.assertEqual(len(corrupt), 1, "corrupt file must be preserved")

    def test_structurally_corrupt_graph_quarantined(self):
        """Regression: valid JSON with wrong structure must quarantine too,
        not crash every subsequent command."""
        gpath = self.mind_dir / "graph.json"
        gpath.write_text('{"nodes": [1, 2, 3], "edges": {}}', encoding="utf-8")
        h = Hippocampus(gpath)
        self.assertEqual(h.nodes, {})
        self.assertTrue(list(self.mind_dir.glob("graph.json.corrupt-*")))
        h.remember("works after recovery")     # must not raise

    def test_wrong_typed_node_dropped(self):
        gpath = self.mind_dir / "graph.json"
        gpath.write_text('{"nodes": {"ab": {"text": 42}, '
                         '"cd": {"text": "good"}}, "edges": {}}',
                         encoding="utf-8")
        h = Hippocampus(gpath)
        self.assertEqual(len(h.nodes), 1)
        h.recall("good")                        # must not raise

    @unittest.skipIf(os.name == "nt", "Windows symlinks require extra privileges")
    def test_atomic_write_refuses_symlink(self):
        target = self.tmp / "real.md"
        target.write_text("x", encoding="utf-8")
        link = self.tmp / "link.md"
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            _atomic_write(link, "attack")


# ─────────────────────────────── dreamer ───────────────────────────────
class TestDreamer(TmpDirTest):
    def parts(self):
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        d = Dreamer(self.mind_dir, h, c)
        return h, c, d

    def test_dream_writes_journal(self):
        h, c, d = self.parts()
        h.remember("some fact")
        memo, text = d.dream()
        self.assertTrue((self.mind_dir / memo).exists())
        self.assertIn("Dream journal", text)

    def test_dream_dry_run_writes_nothing(self):
        h, c, d = self.parts()
        h.remember("some fact")
        before = set(p.name for p in self.mind_dir.rglob("*"))
        memo, text = d.dream(dry_run=True)
        self.assertIsNone(memo)
        after = set(p.name for p in self.mind_dir.rglob("*"))
        self.assertEqual(before, after, "dry run must not create files")

    def test_cluster_promotion_offline(self):
        """Regression: promotion must work with zero network access."""
        h, c, d = self.parts()
        h.remember("deploy script runs on the hetzner server")
        h.remember("hetzner server deploy needs the ssh key")
        h.remember("the deploy to hetzner server takes two minutes")
        h.remember("favorite color is green")
        d.dream()
        self.assertTrue(list(c.files()), "similar memories should promote to cortex")

    def test_contradiction_flagged_not_deleted(self):
        h, c, d = self.parts()
        a = h.remember("the payment provider is stripe with 2 percent fees")
        b = h.remember("the payment provider is paypal with 3 percent fees")
        memo, text = d.dream()
        self.assertIn("possible conflict", text)
        self.assertIn(a, h.nodes)
        self.assertIn(b, h.nodes)
        # linked, not deleted
        self.assertEqual(h.edges[a][b]["relation"], "possible-conflict")

    def test_dream_prunes_stale_and_keeps_reinforced(self):
        h, c, d = self.parts()
        stale = h.remember("stale note nobody used")
        keep = h.remember("core decision recalled daily")
        h.bump([keep]); h.bump([keep])
        h.nodes[stale]["last_accessed"] = (
            datetime.now() - timedelta(days=40)).isoformat()
        h.nodes[keep]["last_accessed"] = (
            datetime.now() - timedelta(days=40)).isoformat()
        d.dream()
        self.assertNotIn(stale, h.nodes)
        self.assertIn(keep, h.nodes)

    def test_signals_consumed_after_dream(self):
        h, c, d = self.parts()
        h.remember("a fact")           # writes a signal
        self.assertTrue((self.mind_dir / "signals.jsonl").exists())
        d.dream()
        self.assertFalse((self.mind_dir / "signals.jsonl").exists())


# ─────────────────────────────── cortex ───────────────────────────────
class TestCortex(TmpDirTest):
    def test_promote_creates_file(self):
        c = Cortex(self.mind_dir / "cortex")
        rel = c.promote("deploy pipeline", "- fact one\n- fact two")
        self.assertTrue((self.mind_dir / rel).exists())

    def test_promote_weird_topic_names(self):
        c = Cortex(self.mind_dir / "cortex")
        c.promote("...///...", "- x")   # regression: old strip('.md') bug
        c.promote("m", "- y")
        self.assertEqual(len(c.files()), 2)


# ─────────────────────────────── active ───────────────────────────────
class TestActiveExport(TmpDirTest):
    def parts(self):
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        a = Active(self.mind_dir, h, c)
        return h, c, a

    def test_export_creates_all_agent_files(self):
        h, c, a = self.parts()
        h.remember("exported fact")
        a.generate(self.tmp)
        a.export_to_agents(self.tmp)
        for f in Active.TARGETS:
            content = (self.tmp / f).read_text("utf-8")
            self.assertIn("exported fact", content)
            self.assertIn(Active.BEGIN, content)

    def test_export_creates_nested_rule_targets(self):
        h, c, a = self.parts()
        h.remember("roo export fact")
        a.generate(self.tmp)
        a.export_to_agents(self.tmp)
        content = (self.tmp / ".roo" / "rules" / "mind.md").read_text("utf-8")
        self.assertIn("roo export fact", content)
        self.assertIn(Active.BEGIN, content)

    def test_export_preserves_user_content(self):
        h, c, a = self.parts()
        (self.tmp / "AGENTS.md").write_text("# My own rules\nBe careful.\n",
                                            encoding="utf-8")
        h.remember("a fact")
        a.generate(self.tmp)
        a.export_to_agents(self.tmp)
        content = (self.tmp / "AGENTS.md").read_text("utf-8")
        self.assertIn("My own rules", content)
        self.assertIn("a fact", content)

    def test_export_preserves_user_content_in_dot_rules(self):
        h, c, a = self.parts()
        (self.tmp / ".cursorrules").write_text("Prefer concise answers.\n",
                                               encoding="utf-8")
        h.remember("cursor fact")
        a.generate(self.tmp)
        a.export_to_agents(self.tmp)
        content = (self.tmp / ".cursorrules").read_text("utf-8")
        self.assertIn("Prefer concise answers", content)
        self.assertIn("cursor fact", content)

    def test_reexport_is_idempotent(self):
        h, c, a = self.parts()
        (self.tmp / "AGENTS.md").write_text("# My own rules\n", encoding="utf-8")
        h.remember("a fact")
        a.generate(self.tmp)
        a.export_to_agents(self.tmp)
        first = (self.tmp / "AGENTS.md").read_text("utf-8")
        a.export_to_agents(self.tmp)
        second = (self.tmp / "AGENTS.md").read_text("utf-8")
        self.assertEqual(first, second)
        self.assertEqual(second.count("My own rules"), 1,
                         "user content must not duplicate on re-export")

    @unittest.skipIf(os.name == "nt", "Windows symlinks require extra privileges")
    def test_export_skips_symlink_targets(self):
        h, c, a = self.parts()
        real = self.tmp / "real-agents.md"
        real.write_text("x", encoding="utf-8")
        (self.tmp / "AGENTS.md").symlink_to(real)
        h.remember("a fact")
        a.generate(self.tmp)
        written = a.export_to_agents(self.tmp)
        self.assertTrue(any("skipped" in w for w in written))
        self.assertEqual(real.read_text("utf-8"), "x")

    @unittest.skipIf(os.name == "nt", "Windows symlinks require extra privileges")
    def test_export_skips_symlink_parent_targets(self):
        h, c, a = self.parts()
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.tmp / ".roo").symlink_to(outside, target_is_directory=True)
        h.remember("a fact")
        a.generate(self.tmp)
        written = a.export_to_agents(self.tmp)
        self.assertTrue(any("symlink parent" in w for w in written))
        self.assertFalse((outside / "rules" / "mind.md").exists())

    def test_working_memory_respects_budget(self):
        h, c, a = self.parts()
        for i in range(50):
            h.remember("fact number %d about topic %d with padding text" % (i, i))
        a.generate(self.tmp)
        size = (self.mind_dir / "ACTIVE.md").stat().st_size
        self.assertLess(size, 6000, "working memory must stay small")


# ─────────────────────────────── CLI ───────────────────────────────
class TestCLI(TmpDirTest):
    def run_cli(self, *args):
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            import io
            from contextlib import redirect_stdout, redirect_stderr
            out, err = io.StringIO(), io.StringIO()
            try:
                with redirect_stdout(out), redirect_stderr(err):
                    code = M.main(list(args))
            except SystemExit as e:
                code = e.code
            return code, out.getvalue(), err.getvalue()
        finally:
            os.chdir(cwd)

    def test_full_cli_lifecycle(self):
        code, out, _ = self.run_cli("init")
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("remember", "the answer is 42")
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("recall", "answer")
        self.assertIn("42", out)
        code, out, _ = self.run_cli("dream", "--dry-run")
        self.assertIn("dry run", out)
        code, out, _ = self.run_cli("status")
        self.assertIn("nodes:", out)

    def test_unknown_command_suggests(self):
        code, _, err = self.run_cli("remembr", "x")
        self.assertNotEqual(code, 0)
        self.assertIn("remember", err)

    def test_typoed_dry_run_flag_refused(self):
        """Regression: `dream --dryrun` must error, never silently run the
        real (destructive) dream."""
        self.run_cli("init")
        code, _, err = self.run_cli("dream", "--dryrun")
        self.assertNotEqual(code, 0)
        self.assertIn("--dry-run", err)

    def test_oversized_memory_does_not_blank_working_memory(self):
        """Regression: one huge memory must not evict everything else
        from ACTIVE.md."""
        self.run_cli("init")
        self.run_cli("remember", "huge " * 3000)
        for i in range(4):
            self.run_cli("remember", "normal fact number %d" % i)
        active = (self.tmp / ".mind" / "ACTIVE.md").read_text("utf-8")
        self.assertIn("normal fact number", active)

    def test_help(self):
        code, out, _ = self.run_cli("--help")
        self.assertEqual(code, 0)
        self.assertIn("recall", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
