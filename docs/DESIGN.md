# mind — design rationale

## The idea in one sentence

A memory for coding agents that imitates the brain: layered, recalled by
trace and spreading activation (not flat search), self-organizing through
dreams between sessions, and portable to any agent through one standard file.

## Binding principles

1. **Three layers** mirroring the brain: working (always injected) +
   hippocampus (light graph) + cortex (consolidated files).
2. **Local trace recall** (radius ≤ 3 hops) + caching — never a full
   PageRank over the whole graph. Cheap by construction.
3. **Forgetting by decay** — every node weakens over time
   (Ebbinghaus: `R = e^(−t/S)`), pruned below a threshold — but never
   within a 45-day grace window of its last access (soak-test finding:
   monthly-cadence facts must survive to their first recall), and pruned
   texts are archived, not destroyed. Stability `S` grows by ~two weeks
   per confirmed recall, so important memories harden and trivia fades.
   This prevents garbage accumulation: in the 180-day soak, junk older
   than the grace window survived 0/256 while core facts survived 15/15.
4. **Timestamps + confidence + conflict flags** — newer/more-trusted wins,
   but conflicts are *flagged*, never auto-deleted; `correct` performs
   explicit reconsolidation with history.
5. **One `init` + automatic export** for every agent
   (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`) behind guard markers.
6. **Writes during the session, dreams between sessions** — the agent
   itself is the live half of the loop; the dreamer is the offline half.
7. **Measure before claiming** — accuracy/latency/size are benchmarked
   (`bench/bench.py`) and published; misses are documented.

## Recall algorithm (the heart)

Four cooperating stages, each added to fix a defect found by live testing:

| stage | technique | fixes |
|---|---|---|
| 1. normalization | bilingual stemming + AR↔EN seed map | "بايثون" ↔ "python" |
| 2. auto-expansion | co-occurrence index + constrained 2-hop PageRank | unknown-unknowns ("database" finds the sqlite node) |
| 3. fusion | **RRF + IDF** over direct & spreading channels | rank ties in multi-hop recall |
| 4. head shaping | hash-embedding re-rank + pattern separation | near-duplicate results crowding top-k |

Plus **pattern completion**: when no direct key matches, fuzzy similarity
over node texts reactivates memories from partial or misspelled cues.

`recall` is a **pure read**: it never writes to disk, so health checks and
repeated queries cannot skew weights. Reinforcement is explicit — recall
prints memory ids and the agent runs `confirm <id>` for hits that actually
answered the question; that hardens the node (Ebbinghaus stability) and
restrengthens its edges, while every dream weakens all edges slightly
(synaptic homeostasis), so unconfirmed connections decay and prune.

## The dream cycle

| phase | biological analogue | what it does |
|---|---|---|
| light sleep | session telemetry | count and clear the session's write signals (remember/link/correct) — currently telemetry the journal reports, not a replay that feeds consolidation; the consolidation inputs are the node/edge weights themselves |
| deep sleep | slow-wave consolidation + synaptic homeostasis | Ebbinghaus decay; prune weak nodes and weak edges |
| REM | recombination | deterministic clustering of related memories → promote recurring clusters to cortex; scan for contradictions and *flag* them |

Everything is deterministic (offline hash embeddings, fixed thresholds):
the same memory state always yields the same plan, `--dry-run` previews it,
and the journal explains every action. No LLM is consulted — consolidation
costs zero tokens and can run in cron forever.

## Failure modes we accept (and why)

- **No true semantic embeddings by default.** Cross-domain synonymy with no
  corpus evidence is missed (the benchmark's one failing query). The
  alternative — bundling a model or requiring API keys — breaks the
  zero-setup promise that makes the tool spread. Pluggable backends can
  come later without breaking the file format.
- **Light Arabic stemming.** A full morphological analyzer would be a
  dependency; the seed dictionary covers the frequent broken plurals and
  the co-occurrence index absorbs the rest.
- **Not for millions of documents.** The graph is JSON on disk; the target
  is personal/project agent memory (10²–10³ nodes) where it is measured at
  sub-millisecond recall.
