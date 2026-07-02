# Changelog

## Unreleased

- Export working memory to Cursor, Windsurf, Cline, and Roo rules files while
  preserving existing user content behind the same guard-marker contract.

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
