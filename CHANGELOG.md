# Changelog

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
