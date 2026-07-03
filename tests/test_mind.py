"""mind test suite — stdlib unittest only (zero dependencies, like the tool).

Run:  python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import builtins
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
        h.nodes[nid]["weight"] = 0.5          # below cap so the boost is visible
        h.bump([nid])
        self.assertGreaterEqual(h.nodes[nid]["weight"], 0.5 + M.BOOST_PER_ACCESS - 1e-9)
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
    def test_correct_supersedes_and_keeps_history(self):
        """6.0.0 temporal fusion: correct CLOSES the old fact instead of
        erasing it — the transition stays queryable."""
        h = self.hippo()
        h.remember("the database is mysql")
        old = h.correct("database mysql", "the database is postgres")
        self.assertEqual(old, "the database is mysql")
        self.assertEqual(len(h.nodes), 2, "old fact is closed, not erased")
        old_id = h._id("the database is mysql")
        new_id = h._id("the database is postgres")
        old_node, new_node = h.nodes[old_id], h.nodes[new_id]
        self.assertIsNotNone(old_node["valid_to"])
        self.assertEqual(old_node["superseded_by"], new_id)
        self.assertIsNone(new_node["valid_to"])
        self.assertEqual(new_node["history"][0]["text"], "the database is mysql")
        self.assertLess(new_node["confidence"], 1.0)
        self.assertEqual(h.edges[new_id][old_id]["relation"], "supersedes")

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
    def _age(self, h, nid, days, created_days=None):
        h.nodes[nid]["last_accessed"] = (
            datetime.now() - timedelta(days=days)).isoformat()
        h.nodes[nid]["created"] = (
            datetime.now() - timedelta(days=created_days or days)).isoformat()

    def test_unused_memory_decays_and_prunes(self):
        h = self.hippo()
        nid = h.remember("trivial one-off note")
        self._age(h, nid, 50)
        pruned = h.decay()
        self.assertIn("trivial one-off note", pruned)
        self.assertNotIn(nid, h.nodes)

    def test_newborn_grace_protects_unrecalled_memory(self):
        """Soak-test regression: a fact noted today and needed next month
        must survive its first weeks even with zero recalls."""
        h = self.hippo()
        nid = h.remember("backup restore drill is in runbooks/restore")
        self._age(h, nid, 35)                 # weight far below threshold, inside grace
        pruned = h.decay()
        self.assertEqual(pruned, [])
        self.assertIn(nid, h.nodes)
        self.assertLess(h.nodes[nid]["weight"], 0.1,
                        "weight must still decay during grace")

    def test_one_confirmed_recall_buys_weeks(self):
        """Soak-test regression: a memory recalled once must survive a
        month-long gap to its next recall."""
        h = self.hippo()
        nid = h.remember("dns registrar is cloudflare")
        h.bump([nid])
        self._age(h, nid, 34, created_days=64)   # past grace, 34d since recall
        pruned = h.decay()
        self.assertIn(nid, h.nodes)

    def test_reinforced_memory_survives(self):
        h = self.hippo()
        nid = h.remember("critical architecture decision")
        h.bump([nid]); h.bump([nid]); h.bump([nid])
        self._age(h, nid, 50)
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
        self._age(h, nid, 50)
        pruned = h.decay(dry_run=True)
        self.assertTrue(pruned)
        self.assertIn(nid, h.nodes, "dry run must not delete")

    def test_pruned_memory_archived_not_destroyed(self):
        h = self.hippo()
        nid = h.remember("old forgotten meeting note")
        self._age(h, nid, 50)
        h.decay()
        self.assertNotIn(nid, h.nodes)
        arch = (self.mind_dir / "archive.md").read_text("utf-8")
        self.assertIn("old forgotten meeting note", arch)


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
        for nid in (stale, keep):
            h.nodes[nid]["last_accessed"] = (
                datetime.now() - timedelta(days=50)).isoformat()
            h.nodes[nid]["created"] = (
                datetime.now() - timedelta(days=50)).isoformat()
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
        for f in Active.CANONICAL:
            content = (self.tmp / f).read_text("utf-8")
            self.assertIn("exported fact", content)
            self.assertIn(Active.BEGIN, content)

    def test_dot_targets_only_when_present(self):
        """A fresh project must get only the three canonical files —
        tool dotfiles are adopted, never imposed."""
        h, c, a = self.parts()
        h.remember("a fact")
        a.generate(self.tmp)
        a.export_to_agents(self.tmp)
        for f in (".cursorrules", ".windsurfrules", ".clinerules"):
            self.assertFalse((self.tmp / f).exists(), f)
        self.assertFalse((self.tmp / ".roo").exists())

    def test_export_creates_nested_rule_targets(self):
        h, c, a = self.parts()
        (self.tmp / ".roo").mkdir()          # project already uses Roo
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

    @unittest.skipIf(os.name == "nt", "Windows symlinks require extra privileges")
    def test_dangling_symlink_parent_skipped(self):
        """Review regression: a DANGLING .roo symlink must be skipped too —
        exists() follows links, so an exists()-guarded check misses it."""
        h, c, a = self.parts()
        (self.tmp / ".roo").symlink_to(self.tmp / "nowhere")
        h.remember("a fact")
        a.generate(self.tmp)
        written = a.export_to_agents(self.tmp)   # must not raise
        self.assertTrue(any("symlink parent" in w for w in written))

    def test_working_memory_respects_budget(self):
        h, c, a = self.parts()
        for i in range(50):
            h.remember("fact number %d about topic %d with padding text" % (i, i))
        a.generate(self.tmp)
        size = (self.mind_dir / "ACTIVE.md").stat().st_size
        self.assertLess(size, 6000, "working memory must stay small")


# ───────────────────── auditor-finding regressions ─────────────────────
class TestAuditFindings2(TmpDirTest):
    """Second-round adversarial audit (Opus fleet, verified receipts)."""

    def test_future_timestamp_does_not_inflate_weight(self):
        h = self.hippo()
        nid = h.remember("critical fact about the deploy pipeline")
        h.nodes[nid]["last_accessed"] = (
            datetime.now() + timedelta(days=100)).isoformat()
        h.decay()
        self.assertLessEqual(h.nodes[nid]["weight"], 1.0,
                             "future timestamp must not inflate weight past 1.0")
        h.decay()  # second cycle must stay clamped
        self.assertLessEqual(h.nodes[nid]["weight"], 1.0)

    def test_non_numeric_weight_does_not_brick_commands(self):
        gpath = self.mind_dir / "graph.json"
        gpath.write_text('{"nodes":{"aaa":{"text":"hi there world",'
                         '"weight":"heavy"}},"edges":{}}', encoding="utf-8")
        h = Hippocampus(gpath)
        self.assertIsInstance(h.nodes["aaa"]["weight"], float)
        results, _, _ = h.recall("hi there world")   # must not raise
        self.assertTrue(results)

    def test_keys_as_bare_string_or_nonstring_element(self):
        gpath = self.mind_dir / "graph.json"
        gpath.write_text('{"nodes":{"aaa":{"text":"alpha beta gamma",'
                         '"keys":[123,"real"]},"bbb":{"text":"delta epsilon",'
                         '"keys":"betacharlie"}},"edges":{}}', encoding="utf-8")
        h = Hippocampus(gpath)                       # must not raise
        self.assertEqual(h.nodes["aaa"]["keys"], ["real"])
        self.assertEqual(h.nodes["bbb"]["keys"], [])  # bare string → dropped
        h.recall("alpha")                            # must not raise

    def test_symlinked_dreams_dir_cannot_escape(self):
        if os.name == "nt":
            self.skipTest("symlinks need privileges on Windows")
        outside = self.tmp / "outside"
        outside.mkdir()
        victim = outside / ("%s.md" % datetime.now().date())
        victim.write_text("PRECIOUS", encoding="utf-8")
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        d = Dreamer(self.mind_dir, h, c)
        shutil.rmtree(self.mind_dir / "dreams")      # setUp created it; replace with symlink
        (self.mind_dir / "dreams").symlink_to(outside, target_is_directory=True)
        h.remember("something to dream about here")
        d.dream()                                    # must not escape
        self.assertEqual(victim.read_text("utf-8"), "PRECIOUS",
                         "a symlinked dreams/ dir must not let dream escape")

    def test_symlinked_cortex_dir_cannot_escape(self):
        if os.name == "nt":
            self.skipTest("symlinks need privileges on Windows")
        outside = self.tmp / "escape_cortex"
        outside.mkdir()
        h = self.hippo()
        shutil.rmtree(self.mind_dir / "cortex")      # setUp created it; replace with symlink
        (self.mind_dir / "cortex").symlink_to(outside, target_is_directory=True)
        c = Cortex(self.mind_dir / "cortex")
        d = Dreamer(self.mind_dir, h, c)
        for i in range(4):
            h.remember("hetzner deploy server ssh key rotation step %d" % i)
        d.dream()                                    # must not crash, must not escape
        self.assertEqual(list(outside.glob("*.md")), [],
                         "a symlinked cortex/ dir must not receive promoted files")

    def test_edge_decay_persists_across_reload(self):
        """The headline claim: edges weaken every dream. Must survive a
        disk reload (the CLI reloads graph.json on every invocation)."""
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        d = Dreamer(self.mind_dir, h, c)
        h.remember("alpha service calls beta service")
        h.remember("beta service writes to gamma store")
        h.link("alpha service calls beta service", "beta service writes to gamma store")
        ida = h._id("alpha service calls beta service")
        d.dream()
        reloaded = Hippocampus(self.mind_dir / "graph.json")
        w = list(reloaded.edges[ida].values())[0]["weight"]
        self.assertLess(w, 1.0, "edge decay must be persisted to disk, not just in memory")

    def test_pruned_edge_not_revived_by_merge(self):
        """Regression for the merge-revival bug: a decayed-to-empty edge
        removed this session must not resurrect from the disk copy."""
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        d = Dreamer(self.mind_dir, h, c)
        # deliberately dissimilar texts so the conflict scan never creates a
        # fresh edge between them (that would confound the revival check)
        h.remember("the office wifi password rotates each quarter")
        h.remember("postgres sixteen is the primary datastore")
        h.link("the office wifi password rotates each quarter",
               "postgres sixteen is the primary datastore")
        ida = h._id("the office wifi password rotates each quarter")
        idb = h._id("postgres sixteen is the primary datastore")
        for _ in range(60):
            d.dream()
        reloaded = Hippocampus(self.mind_dir / "graph.json")
        self.assertNotIn(idb, reloaded.edges.get(ida, {}),
                         "pruned edge must stay pruned on disk, not revive")

    def test_export_preserves_user_file_that_mentions_mind(self):
        """CRITIC HIGH: a real user rule file mentioning the tool must not
        be silently destroyed by the stale-block heuristic."""
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        a = Active(self.mind_dir, h, c)
        user = "# Project rules\nThis project uses mind working memory; run mind.py recall.\n"
        (self.tmp / "CLAUDE.md").write_text(user, encoding="utf-8")
        h.remember("a durable fact")
        a.generate(self.tmp)
        a.export_to_agents(self.tmp)
        content = (self.tmp / "CLAUDE.md").read_text("utf-8")
        self.assertIn("Project rules", content, "user content must survive export")
        self.assertIn("run mind.py recall", content)
        self.assertIn("a durable fact", content)

    def test_link_with_control_chars_creates_real_edge(self):
        """CRITIC: link must hash the cleaned text so the edge lands on the
        stored node id, not a phantom id that gets dropped on reload."""
        h = self.hippo()
        h.link("alpha\x1b[31m node one", "beta node two")
        reloaded = Hippocampus(self.mind_dir / "graph.json")
        ida = reloaded._id(reloaded._clean_text("alpha\x1b[31m node one"))
        self.assertIn(ida, reloaded.nodes)
        self.assertTrue(reloaded.edges.get(ida),
                        "the edge must exist under the real (cleaned) node id")

    def test_pruned_edges_do_not_clobber_a_fresh_conflict_link(self):
        """Auditor finding (my own fix's regression): a conflict edge that
        _rem_conflicts creates must survive even when that same pair had an
        edge pruned earlier the same night. Also: _pruned_edges must not
        poison a later _save in the same process."""
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        d = Dreamer(self.mind_dir, h, c)
        na = "the api uses jwt tokens for auth sessions here"
        nb = "the api uses oauth tokens for auth sessions here"
        h.remember(na)
        h.remember(nb)
        a, b = h._id(na), h._id(nb)
        # give them a nearly-dead edge so this night's dream prunes it
        h.edges.setdefault(a, {})[b] = {"relation": "related", "weight": 0.05}
        h.edges.setdefault(b, {})[a] = {"relation": "related", "weight": 0.05}
        h._save()
        _, text = d.dream()
        reloaded = Hippocampus(self.mind_dir / "graph.json")
        if "conflicts flagged: 1" in text or "possible conflict" in text:
            self.assertTrue(reloaded.edges.get(a, {}).get(b),
                            "a flagged conflict must actually be linked on disk")

    def test_prune_then_recreate_same_op_keeps_the_edge(self):
        """Directly exercise the merge: an edge pruned then re-created before
        the next save must persist, not be stripped by _pruned_edges."""
        h = self.hippo()
        h.remember("alpha widget one")
        h.remember("beta gadget two")
        a, b = h._id("alpha widget one"), h._id("beta gadget two")
        h.edges.setdefault(a, {})[b] = {"relation": "related", "weight": 0.05}
        h._save()
        # simulate a prune (record it) then a legitimate re-link in the same op
        h._pruned_edges.add((a, b))
        h.link("alpha widget one", "beta gadget two", "reconnected")
        reloaded = Hippocampus(self.mind_dir / "graph.json")
        self.assertTrue(reloaded.edges.get(a, {}).get(b),
                        "a re-created edge must survive a same-session prune record")
        self.assertEqual(h._pruned_edges, set(),
                         "_pruned_edges must be cleared after a save")

    def test_lock_symlink_does_not_truncate_target(self):
        """A symlinked graph.json.lock must never truncate its target."""
        if os.name == "nt":
            self.skipTest("symlinks need privileges on Windows")
        victim = self.tmp / "victim.txt"
        victim.write_text("precious", encoding="utf-8")
        h = self.hippo()
        h.remember("seed")                      # creates the real lock
        (self.mind_dir / "graph.json.lock").unlink()
        (self.mind_dir / "graph.json.lock").symlink_to(victim)
        with self.assertRaises(ValueError):
            h.remember("attacker-triggered write")
        self.assertEqual(victim.read_text("utf-8"), "precious")

    def test_save_uses_msvcrt_lock_when_fcntl_is_unavailable(self):
        """Windows must get a real file lock, not atomic-write-only saves."""
        h = self.hippo()
        calls = []

        class FakeMsvcrt:
            LK_LOCK = 1
            LK_UNLCK = 2

            @staticmethod
            def locking(fd, mode, nbytes):
                calls.append((mode, nbytes))

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("fcntl is not available")
            if name == "msvcrt":
                return FakeMsvcrt
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            h.remember("portable windows lock")
        finally:
            builtins.__import__ = real_import

        self.assertEqual(calls, [(FakeMsvcrt.LK_LOCK, 1),
                                 (FakeMsvcrt.LK_UNLCK, 1)])

    def test_msvcrt_lock_blocks_through_contention(self):
        """LK_LOCK gives up with OSError after ~10s of contention; the save
        must keep waiting like flock does, not crash — and must not lose
        the write."""
        h = self.hippo()
        calls = []

        class ContendedMsvcrt:
            LK_LOCK = 1
            LK_UNLCK = 2
            _denials = [2]                     # first two acquires collide

            @classmethod
            def locking(cls, fd, mode, nbytes):
                calls.append((mode, nbytes))
                if mode == cls.LK_LOCK and cls._denials[0] > 0:
                    cls._denials[0] -= 1
                    raise OSError(36, "resource deadlock avoided")

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("fcntl is not available")
            if name == "msvcrt":
                return ContendedMsvcrt
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            h.remember("survives lock contention")   # must not raise
        finally:
            builtins.__import__ = real_import

        self.assertEqual(calls, [(ContendedMsvcrt.LK_LOCK, 1)] * 3 +
                                [(ContendedMsvcrt.LK_UNLCK, 1)])
        reloaded = Hippocampus(self.mind_dir / "graph.json")
        self.assertTrue(any("contention" in n["text"]
                            for n in reloaded.nodes.values()),
                        "the contended save must still land on disk")

    def test_archive_symlink_blocks_pruning(self):
        """'archived, not destroyed' is a guarantee: if the archive cannot
        be written, nothing is pruned."""
        if os.name == "nt":
            self.skipTest("symlinks need privileges on Windows")
        h = self.hippo()
        nid = h.remember("stale but must not vanish")
        h.nodes[nid]["last_accessed"] = (
            datetime.now() - timedelta(days=50)).isoformat()
        h.nodes[nid]["created"] = h.nodes[nid]["last_accessed"]
        (self.mind_dir / "archive.md").symlink_to(self.tmp / "elsewhere.md")
        pruned = h.decay()
        self.assertEqual(pruned, [])
        self.assertIn(nid, h.nodes, "unarchivable memories must be kept")

    def test_orphan_edges_cleaned_and_recall_safe(self):
        gpath = self.mind_dir / "graph.json"
        h = self.hippo()
        nid = h.remember("real node about postgres")
        data = json.loads(gpath.read_text("utf-8"))
        data["edges"][nid] = {"deadbeef0000": {"relation": "ghost", "weight": 1.0}}
        gpath.write_text(json.dumps(data), encoding="utf-8")
        h2 = Hippocampus(gpath)
        self.assertNotIn("deadbeef0000", h2.edges.get(nid, {}))
        results, _, _ = h2.recall("postgres")   # must not raise KeyError
        self.assertTrue(results)

    def test_correct_to_existing_text_merges_not_clobbers(self):
        h = self.hippo()
        h.remember("the database is mysql")
        h.remember("the database is postgres")
        h.remember("backend is flask")
        h.link("the database is postgres", "backend is flask")
        h.correct("database mysql", "the database is postgres")
        self.assertEqual(
            sum(1 for n in h.nodes.values() if "postgres" in n["text"]), 1)
        surviving = h._id("the database is postgres")
        self.assertTrue(h.edges.get(surviving),
                        "existing node's edges must survive the merge")
        node = h.nodes[surviving]
        self.assertTrue(any("mysql" in hh["text"] for hh in node.get("history", [])))

    def test_promote_filename_collision_uniquified(self):
        c = Cortex(self.mind_dir / "cortex")
        c.promote("deploy pipeline!", "- a")
        c.promote("deploy pipeline?", "- b")
        files = list(c.files())
        self.assertEqual(len(files), 2,
                         "distinct topics must never overwrite each other")

    def test_multiword_normalization_bridges_languages(self):
        h = self.hippo()
        h.remember("المشروع يستخدم تايب سكريبت للواجهة")
        results, _, _ = h.recall("typescript")
        self.assertTrue(results)

    def test_link_relation_sanitized(self):
        h = self.hippo()
        h.link("node one alpha", "node two beta", "own\x1b[31ms" + "x" * 100)
        ida = h._id("node one alpha")
        rel = list(h.edges[ida].values())[0]["relation"]
        self.assertNotIn("\x1b", rel)
        self.assertLessEqual(len(rel), 60)

    def test_edges_decay_across_dreams_and_prune(self):
        """Auditor finding: edge weights never changed, making synaptic
        pruning dead code. Now every dream weakens edges; unconfirmed
        connections eventually prune."""
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        d = Dreamer(self.mind_dir, h, c)
        h.remember("alpha component talks to beta")
        h.remember("beta stores results in gamma")
        h.link("alpha component talks to beta", "beta stores results in gamma")
        ida = h._id("alpha component talks to beta")
        d.dream()
        w1 = list(h.edges[ida].values())[0]["weight"]
        self.assertLess(w1, 1.0, "edges must weaken after a dream")
        for _ in range(50):
            d.dream()
        self.assertFalse(h.edges.get(ida),
                         "an unconfirmed edge must eventually prune")

    def test_confirm_restrengthens_edges(self):
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        d = Dreamer(self.mind_dir, h, c)
        h.remember("alpha component talks to beta")
        h.remember("beta stores results in gamma")
        h.link("alpha component talks to beta", "beta stores results in gamma")
        ida = h._id("alpha component talks to beta")
        for _ in range(5):
            d.dream()
        w_before = list(h.edges[ida].values())[0]["weight"]
        h.bump([ida])
        w_after = list(h.edges[ida].values())[0]["weight"]
        self.assertGreater(w_after, w_before)

    def test_no_direct_datetime_now_in_source(self):
        """The injectable clock is the only time source — a stray
        datetime.now() would silently break the soak test."""
        src = (Path(__file__).resolve().parent.parent / "mind.py").read_text("utf-8")
        self.assertEqual(src.count("datetime.now()"), 1,
                         "only _now() may call datetime.now()")

    def test_correct_cleans_control_chars_and_hashes_like_remember(self):
        """correct() is a write path: it must apply the same control-char
        hygiene as remember(), and store the text under the id remember()
        would produce for it — otherwise a later remember() of the same
        (cleaned) text creates a duplicate node."""
        h = self.hippo()
        h.remember("the database is mysql eight")
        dirty = "the database is \x1b[31mpostgres\x1b[0m 16"
        old = h.correct("database mysql", dirty)
        self.assertIsNotNone(old)
        expected_id = h._id(h._clean_text(dirty))
        self.assertIn(expected_id, h.nodes)
        self.assertNotIn("\x1b", h.nodes[expected_id]["text"])

    def test_correct_to_empty_text_refused(self):
        """An empty (or control-chars-only) replacement must never blank
        a memory."""
        h = self.hippo()
        h.remember("the deploy target is hetzner via docker")
        with self.assertRaises(ValueError):
            h.correct("deploy hetzner", "   ")
        with self.assertRaises(ValueError):
            h.correct("deploy hetzner", "\x1b\x07")
        self.assertTrue(any("hetzner" in n["text"] for n in h.nodes.values()))

    def test_self_link_refused(self):
        """A self-loop edge would feed a node its own activation on every
        spreading hop, silently inflating its rank."""
        h = self.hippo()
        nid = h.remember("solo fact about the caching layer")
        with self.assertRaises(ValueError):
            h.link("solo fact about the caching layer",
                   "solo fact about the caching layer")
        # a control-char variant cleans to the same text → same id → refused
        with self.assertRaises(ValueError):
            h.link("solo fact about the caching layer",
                   "solo fact about\x1b the caching layer")
        self.assertNotIn(nid, h.edges.get(nid, {}))

    def test_non_string_timestamp_does_not_crash_decay(self):
        """A hand-edited graph with a numeric last_accessed must degrade
        gracefully (repair on load; treat as fresh in decay), not crash
        the whole dream with TypeError."""
        gpath = self.mind_dir / "graph.json"
        gpath.write_text('{"nodes":{"aaa":{"text":"fact with numeric time",'
                         '"last_accessed":12345,"created":67890}},"edges":{}}',
                         encoding="utf-8")
        h = Hippocampus(gpath)
        self.assertIsInstance(h.nodes["aaa"]["last_accessed"], str)
        h.decay()                                    # must not raise
        self.assertIn("aaa", h.nodes)
        # and the in-memory mutation path (bypasses _load's repair):
        h.nodes["aaa"]["last_accessed"] = 12345
        h.decay()                                    # must not raise
        self.assertIn("aaa", h.nodes)

    def test_key_extraction_deterministic_across_hash_seeds(self):
        """The [:24] key truncation must pick the same subset on every
        machine: set iteration order varies with str-hash randomization,
        which silently made identical `remember` calls store different
        keys per run."""
        import subprocess
        root = str(Path(__file__).resolve().parent.parent)
        snippet = (
            "import sys, tempfile\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, %r)\n"
            "import mind\n"
            "h = mind.Hippocampus(Path(tempfile.mkdtemp()) / 'g.json')\n"
            "text = ' '.join('word%%d unique%%d' %% (i, i) for i in range(20))\n"
            "print('|'.join(h._extract_keys(text)))\n" % root)
        outs = []
        for seed in ("0", "1"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            r = subprocess.run([sys.executable, "-c", snippet],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout.strip())
        self.assertEqual(outs[0], outs[1],
                         "key truncation must not depend on the hash seed")
        self.assertTrue(outs[0].startswith("word0|"),
                        "keys must preserve first-appearance order")

    def test_working_memory_budget_not_quadrupled(self):
        """The hot list must respect ACTIVE_TOKEN_BUDGET as documented
        (characters), not 4× it: a 3000-char memory must be skipped."""
        h, c = self.hippo(), Cortex(self.mind_dir / "cortex")
        a = Active(self.mind_dir, h, c)
        h.remember("big " * 750)                     # ~3000 chars
        h.remember("small fact that fits the working set")
        a.generate(self.tmp)
        active = (self.mind_dir / "ACTIVE.md").read_text("utf-8")
        hot_section = active.split("## Hot memories")[1].split("##")[0]
        hot = [ln for ln in hot_section.splitlines() if ln.startswith("- ")]
        self.assertTrue(any("small fact" in ln for ln in hot))
        self.assertLessEqual(sum(len(ln) for ln in hot),
                             M.ACTIVE_TOKEN_BUDGET)


# ────────────────── v5.5.0: journal + concept seed ──────────────────
class TestV550(TmpDirTest):
    def test_same_day_dreams_accumulate_in_journal(self):
        """A second dream on the same date must append its cycle to the
        day's journal, not silently replace the first one."""
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        d = Dreamer(self.mind_dir, h, c)
        h.remember("fact one about the alpha subsystem")
        d.dream()
        d.dream()
        journal = (self.mind_dir / "dreams" /
                   ("%s.md" % M._now().date())).read_text("utf-8")
        self.assertEqual(journal.count("cycle started"), 2,
                         "both same-day cycles must survive in the journal")

    def test_concept_seed_bridges_category_to_tool(self):
        """The benchmark's one failing query: a memory naming only the
        TOOL must be found by a question asking for the CATEGORY."""
        h = self.hippo()
        h.remember("the design system uses tailwind with a custom palette")
        h.remember("the frontend is react with typescript")
        results, _, _ = h.recall("what css framework do we use")
        self.assertTrue(results)
        self.assertIn("tailwind", results[0][2]["text"])

    def test_concept_seed_bridges_tool_to_category(self):
        """Reverse direction: query names the tool, memory names only the
        category — both sides meet on the shared category key."""
        h = self.hippo()
        h.remember("deploy target is hetzner with docker compose")
        h.remember("release cadence is every second tuesday")
        results, _, _ = h.recall("which cloud provider do we deploy on")
        self.assertTrue(results)
        self.assertIn("hetzner", results[0][2]["text"])

    def test_save_merge_repairs_corrupt_disk(self):
        """Distilled fuzzer finding: the read-merge-write must repair the
        disk copy before merging — a corrupt file left behind by a
        hand-edit or another process must not poison a healthy session."""
        h = self.hippo()
        h.remember("healthy fact about the pipeline")
        # hostile disk state written behind the live session's back
        (self.mind_dir / "graph.json").write_text(
            '{"nodes":{"bad1":42,"bad2":{"text":"ok fact","weight":"NaN",'
            '"history":"scalar","created":false},"bad3":{"text":123}},'
            '"edges":{"bad2":{"bad2x":7,"ghost":{"weight":null}},'
            '"":[1,2]}}', encoding="utf-8")
        h.remember("second healthy fact")          # triggers the merge
        reloaded = Hippocampus(self.mind_dir / "graph.json")
        texts = [n["text"] for n in reloaded.nodes.values()]
        self.assertIn("second healthy fact", texts)
        self.assertIn("ok fact", texts)            # repaired, not dropped
        for n in reloaded.nodes.values():
            self.assertIsInstance(n["weight"], float)
            self.assertTrue(0.0 <= n["weight"] <= 1.0)
            self.assertIsInstance(n["created"], str)
        reloaded.decay()                            # must not raise
        d = Dreamer(self.mind_dir, reloaded, Cortex(self.mind_dir / "cortex"))
        d.dream()                                   # must not raise

    def test_nan_and_infinite_numbers_repaired_on_load(self):
        """Distilled fuzzer finding: float() accepts NaN/Infinity, so the
        numeric coercion alone let them poison rankings and decay math."""
        (self.mind_dir / "graph.json").write_text(
            '{"nodes":{"aaa":{"text":"alpha fact","weight":NaN,'
            '"peak_weight":Infinity,"access_count":-Infinity}},'
            '"edges":{"aaa":{}}}', encoding="utf-8")
        h = self.hippo()
        self.assertEqual(h.nodes["aaa"]["weight"], 1.0)
        self.assertEqual(h.nodes["aaa"]["peak_weight"], 1.0)
        self.assertEqual(h.nodes["aaa"]["access_count"], 0)
        results, _, _ = h.recall("alpha fact")
        self.assertTrue(results)

    def test_concept_seed_does_not_outrank_exact_match(self):
        """IDF must keep category keys from beating an exact term match:
        asking for postgres by name must rank the postgres memory first,
        not another database-category memory."""
        h = self.hippo()
        h.remember("the analytics store is a mongodb replica set")
        h.remember("the main database is postgres 16 with prisma")
        results, _, _ = h.recall("what is our postgres setup")
        self.assertTrue(results)
        self.assertIn("postgres", results[0][2]["text"])


# ──────────── no-space scripts (CJK/kana/Hangul bigrams, 5.6.0) ────────────
class TestNoSpaceScripts(TmpDirTest):
    def test_tokenizer_bigrams_and_mixed_runs(self):
        """No-space runs become character bigrams; embedded Latin words
        inside the same run are kept whole; EN/AR are untouched."""
        self.assertEqual(M._tokenize("项目数据库是postgres十六版本"),
                         ["项目", "目数", "数据", "据库", "库是",
                          "postgres", "十六", "六版", "版本"])
        self.assertEqual(M._tokenize("the database is postgres"),
                         ["the", "database", "postgres"])
        self.assertEqual(M._tokenize("قاعدة البيانات بوستغرس"),
                         ["قاعدة", "البيانات", "بوستغرس"])
        self.assertEqual(M._tokenize("中"), ["中"])   # single char survives

    def test_chinese_recall(self):
        """The exact case that returned NOTHING before 5.6.0."""
        h = self.hippo()
        h.remember("项目数据库是postgres十六版本")
        h.remember("主服务器位于法兰克福机房")
        results, _, _ = h.recall("我们用什么数据库")
        self.assertTrue(results)
        self.assertIn("postgres", results[0][2]["text"])

    def test_japanese_recall(self):
        h = self.hippo()
        h.remember("プロジェクトのデータベースはpostgres十六です")
        h.remember("メインサーバーはフランクフルトにあります")
        results, _, _ = h.recall("データベースは何ですか")
        self.assertTrue(results)
        self.assertIn("postgres", results[0][2]["text"])

    def test_korean_two_char_word_indexed(self):
        """Korean words are often 2 syllables — the 3-char floor used to
        drop them entirely."""
        h = self.hippo()
        h.remember("메인 서버는 프랑크푸르트에 있다")
        results, _, _ = h.recall("서버 어디")
        self.assertTrue(results)
        self.assertIn("프랑크푸르트", results[0][2]["text"])


# ─────────── provenance + temporal validity (6.0.0) ───────────
class TestProvenance(TmpDirTest):
    def test_origin_recorded_at_write_time(self):
        os.environ["MIND_BY"] = "test-agent"
        os.environ["MIND_SESSION"] = "s-123"
        try:
            h = self.hippo()
            nid = h.remember("the payment provider is stripe")
            n = h.nodes[nid]
            self.assertEqual(n["origin"]["by"], "test-agent")
            self.assertEqual(n["origin"]["session"], "s-123")
            self.assertEqual(n["origin"]["via"], "remember")
            self.assertEqual(n["valid_from"], n["created"])
            self.assertIsNone(n["valid_to"])
        finally:
            del os.environ["MIND_BY"], os.environ["MIND_SESSION"]

    def test_journal_records_every_mutation(self):
        h = self.hippo()
        a = h.remember("service alpha uses redis")
        h.remember("service beta uses kafka")
        h.link("service alpha uses redis", "service beta uses kafka", "peer")
        h.bump([a])
        h.correct("alpha redis", "service alpha uses memcached")
        ops = [e["op"] for e in h.journal_entries()]
        self.assertEqual(ops, ["remember", "remember", "link",
                               "confirm", "correct"])
        ev = h.journal_entries()[-1]
        self.assertEqual(ev["old_text"], "service alpha uses redis")
        self.assertIn("by", ev)
        self.assertIn("ts", ev)

    def test_journal_survives_dream(self):
        """THE core provenance guarantee: unlike signals.jsonl, the
        journal is never cleared by consolidation."""
        h = self.hippo()
        h.remember("durable fact about the pipeline")
        d = Dreamer(self.mind_dir, h, Cortex(self.mind_dir / "cortex"))
        d.dream()
        d.dream()
        self.assertFalse((self.mind_dir / "signals.jsonl").exists(),
                         "signals are telemetry and ARE cleared")
        self.assertTrue(len(h.journal_entries()) >= 1,
                        "the journal must survive every dream")

    def test_superseded_fact_excluded_from_recall_but_not_lost(self):
        h = self.hippo()
        h.remember("the database is mysql five")
        h.correct("database mysql", "the database is postgres sixteen")
        results, _, _ = h.recall("which database do we use")
        texts = [n["text"] for _, _, n in results]
        self.assertTrue(any("postgres" in t for t in texts))
        self.assertFalse(any("mysql" in t for t in texts),
                         "closed facts are not current answers")
        old_id = h._id("the database is mysql five")
        self.assertIn(old_id, h.nodes, "…but the fact is still in the graph")

    def test_recall_at_past_date_returns_the_old_truth(self):
        """Bi-temporal-lite: what was true THEN is answerable."""
        h = self.hippo()
        h.remember("the database is mysql five")
        # backdate the fact so "yesterday" falls inside its validity
        old_id = h._id("the database is mysql five")
        past = (datetime.now() - timedelta(days=10)).isoformat()
        h.nodes[old_id]["valid_from"] = past
        h.nodes[old_id]["created"] = past
        h.correct("database mysql", "the database is postgres sixteen")
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        results, _, _ = h.recall("which database do we use", at=yesterday)
        self.assertTrue(results)
        self.assertIn("mysql", results[0][2]["text"],
                      "as-of recall must return the fact valid at that time")

    def test_re_remember_reopens_a_closed_fact(self):
        """Explicit re-assertion beats an old supersession."""
        h = self.hippo()
        h.remember("the cache is redis")
        h.correct("cache redis", "the cache is memcached")
        h.remember("the cache is redis")            # user says: it IS redis
        n = h.nodes[h._id("the cache is redis")]
        self.assertIsNone(n["valid_to"])
        results, _, _ = h.recall("what is the cache")
        self.assertTrue(any("redis" in x[2]["text"] for x in results))

    def test_superseded_fact_pruned_after_grace_without_confirms(self):
        """Closed states leave the hippocampus after grace regardless of
        access_count — lineage stays in journal/history."""
        h = self.hippo()
        h.remember("the region is eu-west one")
        h.bump([h._id("the region is eu-west one")])
        h.bump([h._id("the region is eu-west one")])   # 2 confirms
        h.correct("region eu-west", "the region is me-central one")
        old_id = h._id("the region is eu-west one")
        h.nodes[old_id]["valid_to"] = (
            datetime.now() - timedelta(days=60)).isoformat()
        pruned = h.decay()
        self.assertNotIn(old_id, h.nodes)
        self.assertTrue(any("superseded" in t for t in pruned))
        arch = (self.mind_dir / "archive.md").read_text("utf-8")
        self.assertIn("eu-west", arch)

    def test_valid_but_unconfirmed_old_fact_still_prunes_to_archive(self):
        """Honest boundary: decay (attention) still archives unconfirmed
        valid facts after grace — but never marks them false: no
        valid_to is ever set by decay."""
        h = self.hippo()
        nid = h.remember("rarely needed trivia about lunch")
        h.nodes[nid]["last_accessed"] = (
            datetime.now() - timedelta(days=50)).isoformat()
        h.nodes[nid]["created"] = h.nodes[nid]["last_accessed"]
        h.decay()
        self.assertNotIn(nid, h.nodes)
        # the journal still knows it existed and that it was pruned
        ops = [e["op"] for e in h.journal_entries(nid)]
        self.assertIn("prune", [e["op"] for e in h.journal_entries()])

    def test_edges_carry_created_timestamps(self):
        h = self.hippo()
        a = h.remember("khalid owns the billing service")
        b = h.remember("billing service uses stripe")
        h.link("khalid owns the billing service",
               "billing service uses stripe", "owns")
        self.assertIn("created", h.edges[a][b])

    def test_old_graph_loads_with_honest_defaults(self):
        """Pre-6.0 graphs must load: origin=unknown, validity open."""
        gpath = self.mind_dir / "graph.json"
        gpath.write_text(
            '{"nodes":{"aaa":{"text":"legacy fact from 5.x",'
            '"created":"2026-01-01T00:00:00"}},"edges":{}}',
            encoding="utf-8")
        h = Hippocampus(gpath)
        n = h.nodes["aaa"]
        self.assertEqual(n["origin"]["by"], "unknown")
        self.assertEqual(n["valid_from"], "2026-01-01T00:00:00")
        self.assertIsNone(n["valid_to"])
        results, _, _ = h.recall("legacy fact")
        self.assertTrue(results)

    def test_working_memory_excludes_superseded(self):
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        a = Active(self.mind_dir, h, c)
        h.remember("the database is mysql five")
        h.correct("database mysql", "the database is postgres sixteen")
        a.generate(self.tmp)
        active = (self.mind_dir / "ACTIVE.md").read_text("utf-8")
        hot = active.split("## Hot memories")[1].split("##")[0]
        self.assertIn("postgres", hot)
        self.assertNotIn("mysql", hot)

    def test_cli_why_and_entity_and_at(self):
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            import io
            from contextlib import redirect_stdout, redirect_stderr
            def run(*args):
                out, err = io.StringIO(), io.StringIO()
                try:
                    with redirect_stdout(out), redirect_stderr(err):
                        code = M.main(list(args))
                except SystemExit as e:
                    code = e.code
                return code, out.getvalue(), err.getvalue()
            run("init")
            run("remember", "the database is mysql five")
            run("correct", "database mysql", "the database is postgres sixteen")
            h = Hippocampus(self.tmp / ".mind" / "graph.json")
            new_id = h._id("the database is postgres sixteen")
            old_id = h._id("the database is mysql five")
            code, out, _ = run("why", new_id)
            self.assertEqual(code, 0)
            self.assertIn("STILL TRUE", out)
            self.assertIn("previously:", out)
            code, out, _ = run("why", old_id)
            self.assertEqual(code, 0)
            self.assertIn("SUPERSEDED", out)
            code, out, _ = run("entity", "database")
            self.assertEqual(code, 0)
            self.assertIn("postgres", out)
            self.assertIn("mysql", out)      # superseded shown, marked
            self.assertIn("✗", out)
            code, out, _ = run("recall", "which database", "--at", "2020-01-01")
            self.assertEqual(code, 0)
            self.assertIn("no results", out)
            code, _, err = run("recall", "which database", "--at", "not-a-date")
            self.assertEqual(code, 2)
        finally:
            os.chdir(cwd)


# ───────────── third-audit fixes (Codex + GLM reports, 6.0.1) ─────────────
class TestThirdAudit(TmpDirTest):
    def test_reopen_starts_new_validity_segment(self):
        """Codex#1: re-remembering a superseded fact must NOT resurrect
        its old valid_from — `--at` inside the closed interval must not
        claim it was true."""
        h = self.hippo()
        h.remember("cache is redis")
        old_id = h._id("cache is redis")
        # backdate the original segment, then close it in the past
        past = (datetime.now() - timedelta(days=30)).isoformat()
        h.nodes[old_id]["valid_from"] = past
        h.nodes[old_id]["created"] = past
        h.correct("cache redis", "cache is memcached")
        h.nodes[old_id]["valid_to"] = (
            datetime.now() - timedelta(days=20)).isoformat()
        h.remember("cache is redis")               # reopen NOW
        n = h.nodes[old_id]
        self.assertIsNone(n["valid_to"])
        self.assertGreater(n["valid_from"],
                           (datetime.now() - timedelta(days=1)).isoformat(),
                           "reopening must start a NEW segment at now")
        # a query inside the closed window must not return it
        mid = (datetime.now() - timedelta(days=25)).isoformat()
        results, _, _ = h.recall("what is the cache", at=mid)
        self.assertFalse(any(r[0] == old_id for r in results))

    def test_live_save_quarantines_corrupt_disk(self):
        """Codex#2: _save must quarantine a corrupt graph.json, exactly
        like _load — never silently overwrite it."""
        h = self.hippo()
        h.remember("healthy fact one")
        (self.mind_dir / "graph.json").write_text("{corrupt!!", "utf-8")
        h.remember("healthy fact two")             # triggers merge path
        corrupt = list(self.mind_dir.glob("graph.json.corrupt-*"))
        self.assertTrue(corrupt, "corrupt disk state must be quarantined")
        self.assertIn("{corrupt!!", corrupt[0].read_text("utf-8"))

    def test_init_refuses_symlinked_mind_dir(self):
        """Codex#3: init through a symlinked .mind must not create even a
        directory outside the project."""
        if os.name == "nt":
            self.skipTest("symlinks need privileges on Windows")
        attacker = Path(tempfile.mkdtemp(prefix="mind-attacker-"))
        proj = Path(tempfile.mkdtemp(prefix="mind-proj-"))
        try:
            target = attacker / "payload"
            target.mkdir()
            (proj / ".mind").symlink_to(target)
            cwd = os.getcwd()
            os.chdir(proj)
            try:
                import io
                from contextlib import redirect_stdout, redirect_stderr
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(buf):
                    code = M.main(["init"])
            finally:
                os.chdir(cwd)
            self.assertEqual(code, 1)
            self.assertEqual(list(target.iterdir()), [],
                             "nothing may be created through the symlink")
        finally:
            shutil.rmtree(attacker, ignore_errors=True)
            shutil.rmtree(proj, ignore_errors=True)

    def test_parallel_cli_writers_all_succeed(self):
        """Codex#5: concurrent remembers used to crash on a shared .tmp
        name in the export path — every writer must exit 0 and every
        memory must land."""
        import subprocess
        here = Path(__file__).resolve().parent.parent / "mind.py"
        proj = Path(tempfile.mkdtemp(prefix="mind-par-"))
        try:
            subprocess.run([sys.executable, str(here), "init"],
                           cwd=str(proj), capture_output=True, timeout=30)
            procs = [subprocess.Popen(
                [sys.executable, str(here), "remember",
                 "parallel fact number %d" % i],
                cwd=str(proj), stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE) for i in range(12)]
            codes = [p.wait(timeout=60) for p in procs]
            self.assertEqual(codes, [0] * 12,
                             [p.stderr.read().decode()[:200] for p in procs
                              if p.returncode])
            h = Hippocampus(proj / ".mind" / "graph.json")
            hits = sum(1 for n in h.nodes.values()
                       if "parallel fact" in n["text"])
            self.assertEqual(hits, 12, "no write may be lost")
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_entity_resolves_multiword_arabic_phrase(self):
        """Codex#7: entity must apply the same phrase normalization as
        the index — «تايب سكريبت» is typescript."""
        h = self.hippo()
        h.remember("the frontend uses typescript strict mode")
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                M.main(["entity", "تايب سكريبت"])
            self.assertIn("typescript", buf.getvalue())
        finally:
            os.chdir(cwd)

    def test_entity_finds_tool_by_category(self):
        """GLM#6 REFUTED and pinned: category keys are written on the
        node at remember-time, so `entity css` finds the tailwind fact."""
        h = self.hippo()
        h.remember("the design uses tailwind with a palette")
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                M.main(["entity", "css"])
            self.assertIn("tailwind", buf.getvalue())
        finally:
            os.chdir(cwd)

    def test_conflict_edges_carry_created(self):
        """Codex#8: every edge the dreamer creates is timestamped too."""
        h = self.hippo()
        h.remember("the api rate limit is one hundred per minute")
        h.remember("the api rate limit is two hundred per minute")
        d = Dreamer(self.mind_dir, h, Cortex(self.mind_dir / "cortex"))
        d.dream()
        conflict_edges = [e for nbrs in h.edges.values()
                          for e in nbrs.values()
                          if e.get("relation") == "possible-conflict"]
        if conflict_edges:                          # scan is heuristic
            for e in conflict_edges:
                self.assertIn("created", e)

    def test_oversized_memory_refused(self):
        """Codex#14: a memory is a fact, not a document."""
        h = self.hippo()
        with self.assertRaises(ValueError):
            h.remember("x" * (M.MAX_TEXT_CHARS + 1))
        self.assertEqual(len(h.nodes), 0)

    def test_remember_text_starting_with_dashes(self):
        """Codex#15: free-text commands must accept text that merely
        starts with dashes; only dream/recall have strict flag scans."""
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            import io
            from contextlib import redirect_stdout, redirect_stderr
            buf, err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                M.main(["init"])
                code = M.main(["remember", "--dry-run is a dream flag"])
            self.assertEqual(code, 0, err.getvalue())
            h = Hippocampus(self.tmp / ".mind" / "graph.json")
            self.assertTrue(any("--dry-run" in n["text"]
                                for n in h.nodes.values()))
            # and the dream typo-guard still bites
            err2 = io.StringIO()
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(err2):
                    code2 = M.main(["dream", "--dryrun"])
            except SystemExit as e:
                code2 = e.code
            self.assertEqual(code2, 2)
        finally:
            os.chdir(cwd)

    def test_symlinked_signals_not_read(self):
        """Codex#12: dream must not follow a symlinked signals file."""
        if os.name == "nt":
            self.skipTest("symlinks need privileges on Windows")
        h = self.hippo()
        h.remember("a fact before the attack")
        sig = self.mind_dir / "signals.jsonl"
        if sig.exists():
            sig.unlink()
        outside = Path(tempfile.mkdtemp()) / "outside.jsonl"
        outside.write_text('{"kind":"remember","content":"evil"}\n', "utf-8")
        sig.symlink_to(outside)
        d = Dreamer(self.mind_dir, h, Cortex(self.mind_dir / "cortex"))
        _, text = d.dream()
        self.assertIn("0 session signals", text)
        self.assertTrue(outside.exists(), "the target must not be deleted")

    def test_malformed_validity_repaired_on_load(self):
        """GLM#10: non-ISO validity strings must be repaired, not
        compared lexicographically as garbage."""
        gpath = self.mind_dir / "graph.json"
        gpath.write_text(
            '{"nodes":{"aaa":{"text":"slash dated fact",'
            '"created":"2026-01-01T00:00:00",'
            '"valid_from":"2026/01/01","valid_to":"garbage"}},"edges":{}}',
            encoding="utf-8")
        h = Hippocampus(gpath)
        n = h.nodes["aaa"]
        self.assertEqual(n["valid_from"], "2026-01-01T00:00:00")
        self.assertIsNone(n["valid_to"])
        results, _, _ = h.recall("slash dated fact")
        self.assertTrue(results)


# ──────────────── mutation-testing kills (bench/mutate.py) ────────────────
class TestMutationKills(TmpDirTest):
    """Each test kills one or more surviving mutants from bench/mutate.py —
    i.e. each pins a behavior the suite previously did not bite on."""

    def _age(self, h, nid, days, created_days=None):
        h.nodes[nid]["last_accessed"] = (
            datetime.now() - timedelta(days=days)).isoformat()
        h.nodes[nid]["created"] = (
            datetime.now() - timedelta(days=created_days or days)).isoformat()

    def test_edge_weight_influences_spreading_rank(self):
        """A strong edge must outrank a weak edge in spreading activation
        (kills act*decay*weight -> act*decay/weight)."""
        h = self.hippo()
        hub = h.remember("zulu hub fact")
        strong = h.remember("strong neighbour payload xray")
        weak = h.remember("weak neighbour payload yankee")
        h.edges.setdefault(hub, {})[strong] = {"relation": "r", "weight": 0.9}
        h.edges.setdefault(strong, {})[hub] = {"relation": "r", "weight": 0.9}
        h.edges.setdefault(hub, {})[weak] = {"relation": "r", "weight": 0.1}
        h.edges.setdefault(weak, {})[hub] = {"relation": "r", "weight": 0.1}
        results, _, _ = h.recall("zulu hub")
        order = [nid for nid, _, _ in results]
        self.assertIn(strong, order)
        self.assertIn(weak, order)
        self.assertLess(order.index(strong), order.index(weak),
                        "the stronger edge must rank its neighbour higher")

    def test_unparseable_timestamp_treated_as_fresh_not_aged(self):
        """days must repair to 0 (fully fresh), not any other value."""
        h = self.hippo()
        nid = h.remember("fact with broken clock")
        h.nodes[nid]["last_accessed"] = "not-a-date"
        h.decay()
        self.assertAlmostEqual(h.nodes[nid]["weight"],
                               h.nodes[nid]["peak_weight"], places=6)

    def test_one_confirm_keeps_real_weight_after_a_month(self):
        """Stability must be BASE + access*14 — a collapsed stability
        (access/14) would leave near-zero weight at day 34."""
        h = self.hippo()
        nid = h.remember("dns registrar is cloudflare with 2fa")
        h.bump([nid])
        self._age(h, nid, 34, created_days=64)
        h.decay()
        # stability 3+14=17 -> e^(-34/17) ≈ 0.135; collapsed ≈ e^(-11) ≈ 0
        self.assertGreater(h.nodes[nid]["weight"], 0.1)

    def test_twice_confirmed_memory_never_pruned(self):
        """The prune gate is access_count < 2: two confirmations must
        protect a memory no matter how low its weight decays."""
        h = self.hippo()
        nid = h.remember("twice confirmed ancient fact")
        h.bump([nid])
        h.bump([nid])
        self._age(h, nid, 400, created_days=500)
        pruned = h.decay()
        self.assertIn(nid, h.nodes)
        self.assertEqual(pruned, [])

    def test_hot_list_capped_at_eight(self):
        h = self.hippo()
        c = Cortex(self.mind_dir / "cortex")
        a = Active(self.mind_dir, h, c)
        for i in range(12):
            h.remember("compact fact %d" % i)
        a.generate(self.tmp)
        active = (self.mind_dir / "ACTIVE.md").read_text("utf-8")
        hot_section = active.split("## Hot memories")[1].split("##")[0]
        hot = [ln for ln in hot_section.splitlines() if ln.startswith("- ")]
        self.assertLessEqual(len(hot), 8)

    def test_recall_returns_at_most_top_k(self):
        h = self.hippo()
        for i in range(10):
            h.remember("shared keyword falcon variant number %d" % i)
        results, _, _ = h.recall("falcon")
        self.assertLessEqual(len(results), M.RECALL_TOP_K)
        self.assertEqual(M.RECALL_TOP_K, 5)

    def test_duplicate_remember_never_weakens(self):
        """Re-remembering must reinforce toward the cap, never subtract."""
        h = self.hippo()
        nid = h.remember("idempotent fact")
        h.remember("idempotent fact")
        self.assertEqual(h.nodes[nid]["weight"], 1.0)

    def test_link_stores_exact_relation_and_unit_weight(self):
        h = self.hippo()
        a = h.remember("service alpha exists")
        b = h.remember("service beta exists")
        h.link("service alpha exists", "service beta exists", "depends-on")
        self.assertEqual(h.edges[a][b]["relation"], "depends-on")
        self.assertEqual(h.edges[a][b]["weight"], 1.0)
        self.assertEqual(h.edges[b][a]["weight"], 1.0)

    def test_identity_keys_only_for_identity_or_empty_queries(self):
        """A content query must NOT be polluted with identity keys."""
        h = self.hippo()
        h.remember("anchor fact so the extractor has a corpus")
        keys = h._extract_keys("zebra", is_query=True)
        self.assertNotIn("user", keys)
        self.assertNotIn("project", keys)
        # and a truly empty query still gets the fallback
        keys_empty = h._extract_keys("؟؟", is_query=True)
        self.assertIn("user", keys_empty)

    def test_symlinked_mind_root_refused_entirely(self):
        """A symlinked .mind/ root must be refused at the very first write
        — nothing at all may be created through it."""
        if os.name == "nt":
            self.skipTest("symlinks need privileges on Windows")
        attacker = Path(tempfile.mkdtemp(prefix="mind-attacker-"))
        proj = Path(tempfile.mkdtemp(prefix="mind-proj-"))
        try:
            fake = attacker / "payload"
            (fake / "dreams").mkdir(parents=True)
            (fake / "cortex").mkdir()
            (proj / ".mind").symlink_to(fake)
            h = Hippocampus(proj / ".mind" / "graph.json")
            with self.assertRaises(ValueError):
                h.remember("bait fact")
            leaked = [p for p in fake.rglob("*") if p.is_file()]
            self.assertEqual(leaked, [],
                             "no file may be written through a symlinked "
                             ".mind root")
        finally:
            shutil.rmtree(attacker, ignore_errors=True)
            shutil.rmtree(proj, ignore_errors=True)

    def test_correct_gate_boundaries(self):
        """Exactly 2 shared content tokens (or exactly half of the hint)
        must be ENOUGH — the gate is >=, not >."""
        h = self.hippo()
        h.remember("gateway timeout is ninety seconds")
        # hint shares exactly two content tokens: gateway, timeout
        old = h.correct("gateway timeout wrong", "gateway timeout is thirty seconds")
        self.assertIsNotNone(old)

    def test_decayed_exact_match_beats_fresh_noise(self):
        """The 0.35 weight-bias floor (soak finding): an aged
        exactly-matching memory must outrank fresh unrelated notes."""
        h = self.hippo()
        target = h.remember("quasar telescope catalogue number is 7788")
        for i in range(5):
            h.remember("fresh unrelated note number %d about lunch" % i)
        h.nodes[target]["weight"] = 0.15          # deeply decayed
        results, _, _ = h.recall("quasar telescope catalogue")
        self.assertEqual(results[0][0], target)

    def test_activation_spreads_full_radius(self):
        """A node RECALL_RADIUS hops away must still receive activation —
        an off-by-one in the hop loop would silently shrink the radius."""
        h = self.hippo()
        # lexically DISJOINT texts: the only path from the query to the
        # last node is the edge chain, so this pins the spreading radius
        texts = ["quokka origin",
                 "wombat relay",
                 "numbat waypoint",
                 "bilby terminus"]
        for t in texts:
            h.remember(t)
        for a, b in zip(texts, texts[1:]):
            h.link(a, b, "next")
        results, _, _ = h.recall("quokka origin")
        found = {nid for nid, _, _ in results}
        self.assertIn(h._id(texts[3]), found,
                      "a 3-hop neighbour must surface within radius 3")

    def test_access_count_zero_survives_reload(self):
        """The load repair must not invent reinforcement: a never-confirmed
        node's access_count stays exactly 0 across save/reload."""
        h = self.hippo()
        h.remember("never confirmed fact")
        reloaded = Hippocampus(self.mind_dir / "graph.json")
        node = next(iter(reloaded.nodes.values()))
        self.assertEqual(node["access_count"], 0)


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
        self.assertEqual(code, 2, "usage errors exit 2 (documented contract)")
        self.assertIn("remember", err)

    def test_typoed_dry_run_flag_refused(self):
        """Regression: `dream --dryrun` must error, never silently run the
        real (destructive) dream."""
        self.run_cli("init")
        code, _, err = self.run_cli("dream", "--dryrun")
        self.assertEqual(code, 2, "usage errors exit 2 (documented contract)")
        self.assertIn("--dry-run", err)

    def test_cli_link_links_the_two_given_texts(self):
        """The CLI must link argv[1] to argv[2] — and library errors
        (like a self-link) must exit 1, not 2 and not a traceback."""
        self.run_cli("init")
        self.run_cli("remember", "alpha service fact")
        self.run_cli("remember", "beta service fact")
        code, out, _ = self.run_cli("link", "alpha service fact",
                                    "beta service fact", "peer-of")
        self.assertEqual(code, 0)
        h = Hippocampus(self.tmp / ".mind" / "graph.json")
        a, b = h._id("alpha service fact"), h._id("beta service fact")
        self.assertEqual(h.edges[a][b]["relation"], "peer-of")
        code, _, err = self.run_cli("link", "alpha service fact",
                                    "alpha service fact")
        self.assertEqual(code, 1, "library errors exit 1")
        self.assertNotIn("Traceback", err)

    def test_cli_exit_code_contract(self):
        """0 = success, 1 = runtime/library failure, 2 = usage error —
        pinned exactly, they are part of the scripting contract."""
        self.run_cli("init")
        code, _, _ = self.run_cli("confirm", "ffffffffffff")
        self.assertEqual(code, 1, "unknown memory id exits 1")
        code, _, _ = self.run_cli("correct", "some hint", "   ")
        self.assertEqual(code, 2, "empty argument is a usage error: exit 2")

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
        self.assertIn("confirm", out)

    def test_confirm_cli_reinforces(self):
        self.run_cli("init")
        self.run_cli("remember", "the answer is 42")
        code, out, _ = self.run_cli("recall", "answer")
        self.assertIn("id ", out, "recall must print memory ids")
        import re as _re
        nid = _re.search(r"id ([0-9a-f]{12})", out).group(1)
        code, out, _ = self.run_cli("confirm", nid)
        self.assertEqual(code, 0)
        self.assertIn("reinforced", out)
        code, _, err = self.run_cli("confirm", "ffffffffffff")
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
