# Changelog

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
