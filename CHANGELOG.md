# Changelog

## 5.5.0 — 2026-07-02

The capability + verification release: recall gains a concept layer (the
benchmark is now 20/20), Windows gets real file locking, and two new
harnesses — a fuzzer and a mutation tester — each caught real defects on
their first run that six earlier audit rounds had missed.

- **Concept seed (recall capability): ~90 curated tool→category mappings**
  (tailwind→css, hetzner→cloud, sentry→errors, jwt→auth...), applied to
  memories and queries alike, so a question asked by category finds a
  memory that only names the tool — the benchmark's one standing miss.
  recall@1 is now **1.00** at 100 and 1,000 nodes (was 0.95). Polysemous
  words (black, express, spring, phoenix, prettier, oracle) are excluded
  by design: a false category on an everyday sentence is worse than a
  missed synonym. IDF keeps category keys from outranking exact matches
  (regression-tested).
- **Real file locking on Windows** (#9 — thanks @therealclvn):
  `msvcrt.locking` fallback when `fcntl` is unavailable, wrapped in a
  retry loop so it blocks indefinitely under contention exactly like
  `flock` (`LK_LOCK` alone gives up after ~10s with `OSError`); the
  locked read-merge-write path is preserved instead of degrading to
  atomic-write-only saves. Simulated-backend + contention regression
  tests; exercised for real on the `windows-latest` CI matrix.
- **Fuzzer** (`bench/fuzz.py`, seeded, deterministic, runs in CI): 420
  adversarial cases against the graph file and the CLI. Its first run
  caught a real defect: **the read-merge-write imported raw disk content
  past all of `_load`'s validation**, so one corrupt `graph.json` (a
  hand-edit, a buggier tool) poisoned a healthy session's next save.
  Node/edge repair is now a single shared path used by both load and
  merge; NaN/Infinity are repaired too (`float()` accepts them), history
  must be a list, edge weights are clamped finite.
- **Mutation tester** (`bench/mutate.py`): AST-level first-order defects
  (flipped comparisons/boolean ops/arithmetic, nudged constants), full
  suite run per mutant. First run: raw kill rate 33% on a 120-mutant
  sample — exposing **17 behaviors the suite never actually pinned**, each
  now locked by a dedicated test (edge weight must influence ranking, two
  confirmations protect a memory forever, stability math, full spreading
  radius, exact CLI exit codes, exact relation storage, TOP_K, hot-list
  cap...). Raw kill rate after: 46%; surviving mutants are triaged in the
  tool's output — unreachable `get()` defaults, display-only constants,
  and ranking-calibration values guarded by the CI benchmark gate
  (recall@1 ≥ 0.9) rather than by unit assertions.
- **Boundary fix (found by the new kill tests): through a symlinked
  `.mind/` root, the lock file was created outside the project** — the
  one write that escaped. The lock file's parent chain is now checked
  before creation; nothing is written through a symlinked root.
- **Dream journal accumulates**: a second dream on the same date appends
  its cycle to the day's journal instead of silently replacing it.
- Honest soak display: "hot slots" now counts only the Hot-memories
  section — 8/8 core facts, not 8/23 against instruction bullets.
- 118 tests (was 94).

## 5.4.2 — 2026-07-02

Full-repository precision review (every file, line by line). Six defects
found and fixed, each with a regression test that fails on 5.4.1 (94 total):

- **`correct` skipped the control-char sanitizer** that `remember` applies:
  a corrected memory could carry ANSI escapes back to the terminal on
  recall (violating the SECURITY.md hygiene claim) and was stored under an
  id `remember()` would never produce, so re-remembering the same cleaned
  text created a duplicate node. An empty or control-chars-only
  replacement also silently blanked the memory — both now refused (CLI
  validates too).
- **`link` accepted self-links**, creating a self-loop edge that fed a
  node its own activation on every spreading hop and silently inflated
  its rank.
- **Working memory grew to 4× its documented size**: the hot-list budget
  applied a leftover ×4 token→char conversion on top of a constant already
  expressed in characters, so ACTIVE.md could reach ~800 tokens while the
  README promised ~200.
- **Key extraction was nondeterministic across machines**: the [:24]
  truncation iterated a set, whose order varies with str-hash
  randomization — identical `remember` calls could store different key
  subsets per run/machine. Keys now preserve first-appearance order.
- **A non-string timestamp crashed `dream`**: `decay` caught only
  ValueError, so a hand-edited numeric `last_accessed` raised TypeError.
  Repaired on load and tolerated in decay, like every other field.
- Cosmetic: redundant dict copy in the save merge.

## 5.4.1 — 2026-07-02

Follow-up to the 5.4.0 edge-merge fix (caught by a third adversarial pass):

- The `_pruned_edges` stripping ran *after* live edges were merged, so it
  could clobber an edge legitimately (re)created the same session — most
  visibly the `possible-conflict` link `dream` creates when a conflicting
  pair's old edge decayed the same night. Stripping now happens on the disk
  copy *before* the live merge, and `_deleted`/`_pruned_edges` are cleared
  after each successful save so a stale prune record can't poison a later
  write. Two regression tests added (88 total).

## 5.4.0 — 2026-07-02

Second adversarial audit (an Opus-4.8 fleet: 17 reviewers with distinct
methodologies, each finding independently reproduced-or-refuted, plus a
completeness critic). Every confirmed defect fixed with a regression test
(86 tests total):

- **Data-loss (critical): `export_to_agents` could destroy a user's whole
  CLAUDE.md/AGENTS.md/GEMINI.md** if it merely contained the substring
  "mind working memory" or began with "# ACTIVE.md" — no markers, no
  backup, no warning. The stale-block heuristic is now a strict structural
  match on our exact generated header, so real user files that mention the
  tool are preserved.
- **Security: parent-symlink escape.** `_atomic_write` guarded only the
  final path; a symlinked `.mind/dreams` or `.mind/cortex` let dream/promote
  overwrite arbitrary files outside the project. A parent-directory symlink
  walk (`boundary=`) now protects every internal write, and dream/promote
  degrade gracefully instead of crashing on an unsafe dir.
- **Weight inflation: a future `last_accessed`** (clock skew / cross-machine
  sync — this is synced memory, `_now()` is naive local time) made decay's
  retention exceed 1.0 and inflated a node's weight unboundedly and
  permanently. Decay now clamps to `[0,1]` and treats future timestamps as
  fresh.
- **Edge decay was never persisted.** The "every dream weakens all edges"
  claim held in-process but the CLI reloads from disk each run, so edges
  never actually decayed; dream now saves after the edge pass. Fixing this
  surfaced a latent **merge-revival bug** (a pruned edge resurrected from
  the disk copy because edge deletions weren't tracked) — now tracked and
  stripped in the read-merge-write.
- **Corrupt-graph robustness.** A non-numeric `weight`, or a `keys` field
  that's a bare string or contains a non-string element, no longer bricks
  every command — numeric fields are coerced and keys are validated on load.
- **Phantom edges: `link` hashed raw text** while `remember` hashed the
  control-char-cleaned text, so an edge on text with control chars landed on
  a non-existent id and was dropped on reload. Both now share one
  sanitizer.
- **Honest docs:** the soak now also shells out to the real CLI (argv +
  disk-reload path); the "light sleep — signal replay" phase is documented
  as the telemetry it actually is, not a replay.

## 5.3.0 — 2026-07-02

Audit-hardening release. Three independent code audits (two external
models + a line-by-line review) were verified claim by claim; every
confirmed finding is fixed here with a regression test:

- **Reinforcement is now agent-reachable**: `recall` prints memory ids and
  the new `confirm <id>` command performs the bump — previously `bump()`
  existed only as a library call no agent could invoke, so "recalled often
  → hardens" could not happen through the CLI. Exported agent
  instructions teach the confirm loop.
- **Edge weights are now dynamic**: every dream weakens all edges
  (synaptic homeostasis, ×0.95), `confirm` restrengthens the confirmed
  node's edges; unconfirmed connections prune after ~45 dreams —
  synaptic pruning was previously dead code for `link` edges (always 1.0).
- **Durability**: fsync before rename in all atomic writes (power-loss
  safety was previously overstated); the lock file is opened O_NOFOLLOW
  (a symlinked `graph.json.lock` could truncate its target).
- **Archive guarantee**: pruning now archives first and skips deletion
  entirely if the archive is unwritable (a symlinked `archive.md`
  previously caused silent data loss).
- **Graph robustness**: edges referencing missing nodes are dropped on
  load and filtered in recall (orphan edges could crash recall with
  KeyError after partial corruption).
- **`correct` merge semantics**: correcting a memory into the text of an
  existing memory now merges histories/edges/reinforcement instead of
  clobbering the existing node.
- **Cortex collision safety**: two topics sanitizing to the same filename
  no longer overwrite each other (content-hash suffix).
- **Multi-word normalization fixed** ("تايب سكريبت" → typescript now
  matches — phrases are replaced before tokenization); `link` relations
  are sanitized like memory texts.
- CI: actions pinned by commit SHA, least-privilege permissions,
  Windows added to the test matrix.
- 77 tests. Independent-audit claims that did **not** reproduce are
  documented in the repo discussions rather than silently ignored.

## 5.2.0 — 2026-07-02

- **New export targets** (first community contribution — thanks
  [@Abhinav-0311](https://github.com/Abhinav-0311), #6): `.cursorrules`,
  `.windsurfrules`, `.clinerules` and `.roo/rules/mind.md` (Cursor,
  Windsurf, Cline, Roo), with symlink-parent protection for nested paths
  and Windows-portable atomic writes.
- Follow-up hardening on top of #6: tool dotfiles are **adopted, not
  imposed** — written only when the project already has the rule file (or
  a `.roo/` directory), so fresh projects stay clean with the three
  canonical files; dangling-symlink parents are now skipped too
  (`exists()` follows links and missed them).

## 5.1.0 — 2026-07-02

Soak-hardened forgetting. A new 180-day simulated-usage soak test
(`bench/soak.py` — the real code driven through an injected clock) caught
two calibration bugs that unit tests could not:

- **Facts needed monthly died before their first recall** (pruned by the
  nightly dream one day before the scheduled recall). Fix: a 45-day grace
  window — no memory is pruned within 45 days of its last access. Weight
  still decays during grace, so unproven memories fade from rankings.
- **Decayed weight vetoed exact matches**: a strongly-matching but aged
  memory ranked below fresh noise, so it could never earn its first
  reinforcement. Fix: weight now biases ranking (floor 0.35) instead of
  multiplying it to zero.
- Stability per confirmed recall raised to ~two weeks (was 5 days).
- Pruned memories are now archived to `.mind/archive.md`, never destroyed.
- Soak results at day 180: core-fact survival 15/15 across daily/weekly/
  monthly tiers, stale junk surviving 0/256, graph bounded, recall 0.37 ms.
  The soak now runs in CI.

## 5.0.0 — 2026-07-02

First public release. The tool was developed and battle-tested privately
through 17+ iterations (v1.0 → v4.x) on real agents (Codex + MiniMax-M3),
with every defect below found by live testing, then consolidated into a
single zero-dependency file for release:

- **Recall**: spreading activation (≤3 hops) + IDF + Reciprocal Rank Fusion;
  offline hash-embedding re-rank; pattern completion (fuzzy cue fallback);
  pattern separation (near-duplicate results diversified out of top-k).
- **Forgetting**: Ebbinghaus curve `R = e^(−t/S)`; stability grows with
  confirmed recalls; `recall` is pure read — reinforcement only via `bump()`.
- **Dreaming**: light (session signals) → deep (decay + synaptic edge
  pruning) → REM (deterministic clustering → cortex promotion +
  contradiction flagging). `--dry-run` previews everything.
- **Reconsolidation**: new `correct` command — rewrite a wrong memory,
  history preserved on the node, confidence lowered until re-confirmed.
- **Portability**: guard-marked export to AGENTS.md / CLAUDE.md / GEMINI.md,
  idempotent, preserves user content, refuses symlinks, atomic writes.
- **Fixed during consolidation**: offline dream promotion (previously
  required a network embedding backend — clusters never promoted offline),
  cortex filename sanitization bug, duplicated key-extraction loop, and a
  user-content duplication bug on re-export (caught by the new test suite).
- 58 unit tests (stdlib only) + reproducible benchmark (`bench/bench.py`).
