# mind — brain-like memory for any coding agent

[![tests](https://github.com/Da7-Tech/mind/actions/workflows/ci.yml/badge.svg)](https://github.com/Da7-Tech/mind/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](https://github.com/Da7-Tech/mind/blob/main/.github/workflows/ci.yml)
[![deps](https://img.shields.io/badge/dependencies-0-brightgreen)](https://github.com/Da7-Tech/mind/blob/main/mind.py)
[![recall@1](https://img.shields.io/badge/recall%401-0.95_measured-success)](https://github.com/Da7-Tech/mind/blob/main/bench/bench.py)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![English](https://img.shields.io/badge/README-English-2ea44f)](README.md)
[![العربية](https://img.shields.io/badge/README-%EF%BA%8D%EF%BB%9F%EF%BB%8C%EF%BA%AE%EF%BA%91%EF%BB%B4%EF%BA%94-8A2BE2)](README.ar.md)

**One Python file. Zero dependencies. Zero API keys. Fully offline. Bilingual (EN + AR).**

Your coding agent forgets everything between sessions. `mind` gives it a memory
that works the way yours does: a weighted concept graph that recalls by
**spreading activation** (not flat search), forgets by the **Ebbinghaus curve**
(unused memories fade, reinforced ones harden), and reorganizes itself while
you sleep through a **deterministic dream cycle** — no LLM calls, no token
bill, every decision explained in a journal you can read.

It plugs into **every agent at once**: one memory, exported to `AGENTS.md`
(Codex, Cursor, Zed, ...), `CLAUDE.md` (Claude Code), and `GEMINI.md`.

```bash
curl -O https://raw.githubusercontent.com/Da7-Tech/mind/main/mind.py
python3 mind.py init
python3 mind.py remember "the project database is postgres 16"
python3 mind.py recall "which database do we use"
# recall for "which database do we use" — 1 results [0.20 ms]
#   1. [0.033] (direct) the project database is postgres 16
python3 mind.py dream        # between sessions: forget, consolidate, promote
```

That's the whole install. No server, no vector store, no embedding model,
no configuration file.

## Measured, not vibes

Every number below comes from `python3 bench/bench.py` — rerun it yourself
(Python 3.14, Apple M-series; 20 bilingual queries with known answers
against distractor-filled graphs):

| graph size | recall@1 | recall@5 | median latency | p95 |
|---|---|---|---|---|
| 100 nodes | **0.95** | 0.95 | **0.46 ms** | 2.2 ms |
| 1,000 nodes | **0.95** | 0.95 | 2.2 ms | 12.4 ms |

Dream determinism: **PASS** — identical memory state always produces the
identical consolidation plan.

**180-day soak** (`bench/soak.py` — the real code driven through an injected
clock with a realistic workload: daily/weekly/monthly facts + 357 junk notes
+ a dream every night): core-fact survival **15/15** across all cadence
tiers, junk older than the grace window surviving: **0/256**, graph size
bounded (~106 nodes), recall on the aged graph 0.37 ms. The soak caught two
real calibration bugs before release (facts pruned one day before their
first monthly recall; decayed weight vetoing exact matches) — both fixed
with regression tests. The one benchmark miss (`"what css framework"`
→ tailwind) is an honest limitation documented below, not hidden.

Test suite: **63 tests**, stdlib `unittest`, `python3 -m unittest discover -s tests` —
including regression tests for concurrency (parallel writers must not lose
each other's memories), destructive-op gating, and corrupt-graph recovery.

## How it works — three layers, like a brain

```
Layer 1  WORKING MEMORY   .mind/ACTIVE.md  → injected into agent instruction files
         the ~200 tokens the agent always sees: hottest memories + cortex index

Layer 2  HIPPOCAMPUS      .mind/graph.json → weighted concept graph
         recall = spreading activation (≤3 hops) fused with direct keyword
         matches via Reciprocal Rank Fusion + IDF, re-ranked by offline
         hash embeddings; near-duplicate results are diversified (pattern
         separation); fuzzy fallback finds memories from partial cues
         (pattern completion)

Layer 3  CORTEX           .mind/cortex/*.md → consolidated durable knowledge
         fed by the dreamer when a cluster of related memories recurs

DREAMER  between sessions  python3 mind.py dream [--dry-run]
         light sleep  ingest session signals
         deep sleep   Ebbinghaus decay  R = e^(−t/S)  — stability S grows
                      with each confirmed recall; weak unused nodes pruned;
                      weak edges pruned (synaptic pruning)
         REM          cluster related memories → promote recurring themes
                      to cortex; flag contradictions (never auto-delete)
```

Wrong memory? **Reconsolidation** is built in:

```bash
python3 mind.py correct "database mysql" "the database is postgres 16"
# old text kept in node history; confidence lowered until re-confirmed
```

## Why not just use ___?

| | spreading-activation recall | sleep consolidation | works with any agent | zero setup / zero keys | consolidation costs $0 (no LLM) |
|---|---|---|---|---|---|
| mem0 | ✗ | ✗ | ✗ (SDK) | ✗ OSS self-host or cloud, but needs LLM + embedder keys | ✗ |
| Letta (MemGPT) | ✗ | ✓ sleep-time compute | ✗ (own server) | ✗ full platform | ✗ burns tokens |
| Zep / Graphiti | ~ graph traversal | ✗ | ✗ | ✗ Neo4j + LLM keys | ✗ |
| HippoRAG 2 | ✓ PageRank | ✗ | ✗ (batch RAG lib) | ✗ GPU/API | ✗ |
| OpenClaw dreams | ✗ | ✓ | ✗ (OpenClaw only) | ✓ inside OpenClaw | ✗ burns tokens |
| claude-mem | ✗ | ✗ compression | ~ several agents | ✗ Bun + worker + Chroma | ✗ |
| **mind** | **✓** | **✓ deterministic** | **✓ AGENTS/CLAUDE/GEMINI + rules files** | **✓ one file** | **✓** |

Honest note: [Brain Memory](https://github.com/omelas-tech/brain) is the
closest project in spirit (files + decay + sleep phases) — credit where due.
`mind` differs in being a single copy-able file, bilingual EN/AR at the
tokenizer level, fully deterministic in consolidation, and shipping with a
reproducible benchmark instead of claims.

## Commands

| command | what it does |
|---|---|
| `init` | create `.mind/` + export agent files |
| `remember "text"` | add a memory node |
| `link "a" "b" [rel]` | connect two memories (weighted edge) |
| `recall "question"` | spreading-activation recall |
| `correct "old" "new"` | reconsolidate a wrong memory (history kept) |
| `dream [--dry-run]` | run the sleep cycle; journal in `.mind/dreams/` |
| `export` | regenerate agent instruction files |
| `status` | health report |

Reinforcement is explicit: `recall` is pure read (repeated queries can't
skew weights); agents confirm useful hits via the `bump()` API.

## Safety properties

- **Atomic, symlink-refusing writes** everywhere (no torn files, no symlink attacks)
- **Never silently destroys data**: corrupt graphs are quarantined, not erased;
  memories pruned by decay are archived to `.mind/archive.md`, not destroyed;
  user content in exported agent files is preserved outside guard markers
- **`dream --dry-run`** previews the full plan without touching disk
- **File-locked saves** — safe under concurrent agent processes
- Memory files are plain JSON + Markdown: `git diff` them, sync them, read them

## Honest limitations

- Recall is lexical + graph-structural. Cross-domain synonymy with no corpus
  evidence (e.g. "css" → a memory that only says "tailwind") is missed —
  that's the benchmark's one failing query. True embeddings would fix it at
  the cost of the zero-dependency promise; a pluggable backend is on the roadmap.
- Arabic stemming is light (prefix/suffix + broken-plural seed), not a full
  morphological analyzer.
- Optimized for personal/project agent memory (10²–10³ nodes), not
  enterprise RAG over millions of documents — use a real graph DB for that.
- A fact recalled fewer than twice and untouched for longer than the 45-day
  grace window decays out of the graph (into the archive). Facts you need
  less often than ~every six weeks should live in cortex notes, not the
  hippocampus — that's the brain deal: use it or archive it.

## Using with Hermes, Claude Code, Codex, Gemini CLI...

`mind init` writes the working memory into `AGENTS.md`, `CLAUDE.md`,
`GEMINI.md`, `.cursorrules`, `.windsurfrules`, `.clinerules`, and
`.roo/rules/mind.md` with guard markers, preserving your existing content. Any
agent that reads those files gets the memory and the instructions to use it —
nothing else to configure. A ready-made Hermes skill lives in [`SKILL.md`](SKILL.md).

## Development

```bash
python3 -m unittest discover -s tests   # 63 tests
python3 bench/bench.py                  # reproduce the numbers above
```

Design rationale: [docs/DESIGN.md](docs/DESIGN.md) ·
Arabic README: [README.ar.md](README.ar.md) · License: MIT

## Contributing

Issues and PRs welcome — the [roadmap issues](https://github.com/Da7-Tech/mind/issues)
are scoped and ready to pick up. Ground rules: keep `mind.py` a single
stdlib-only file, every change needs a test, and claims need measurements
(`bench/bench.py` must stay green). Questions →
[Discussions](https://github.com/Da7-Tech/mind/discussions).

If `mind` remembers something useful for you, a ⭐ helps other agents' humans find it.
