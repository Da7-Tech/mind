---
name: mind
description: >-
  Brain-like project memory for agents: spreading-activation recall,
  Ebbinghaus forgetting, deterministic dream consolidation, and export to
  AGENTS.md/CLAUDE.md/GEMINI.md. One Python file, zero dependencies, zero
  API keys, fully offline, bilingual (EN/AR).
version: 5.2.0
author: Da7-Tech
license: MIT
platforms: [linux, macos]
prerequisites:
  commands: [python3, curl]
metadata:
  hermes:
    tags: [Memory, Knowledge-Graph, Consolidation, Offline, Local-First]
    requires_toolsets: [terminal]
    category: autonomous-ai-agents
    homepage: https://github.com/Da7-Tech/mind
---

# mind — Brain-Like Project Memory

`mind` gives any project a persistent, self-organizing memory that lives in
`.mind/` and is exported into `AGENTS.md`/`CLAUDE.md`/`GEMINI.md`. It
complements Hermes' built-in memory: the built-in memory stores durable
*global* facts about the user (small, curated, always in context), while
`mind` stores *per-project* knowledge as a weighted concept graph with
recall, forgetting, and consolidation.

This skill wraps the standalone open-source tool
(https://github.com/Da7-Tech/mind — one stdlib-only Python file). No servers,
no API keys, no configuration. All commands below are real and verified.

## When to Use

- The user asks you to remember project facts, decisions, or context across sessions
- You need to look up a project fact you do not have in context ("what database do we use?")
- The user says a stored fact is wrong and gives the correction
- Housekeeping between sessions ("clean up / consolidate the project memory")
- Do NOT use it for durable personal facts about the user — those belong in
  Hermes' built-in memory tool

## Setup (once per project)

```bash
cd <project>
curl -fsSLO https://raw.githubusercontent.com/Da7-Tech/mind/main/mind.py
python3 mind.py init
```

`init` creates `.mind/` and writes guard-marked memory blocks into
`AGENTS.md`, `CLAUDE.md`, `GEMINI.md` — existing user content in those files
is preserved outside the markers. Projects already using Cursor, Windsurf,
Cline or Roo get their rule files (`.cursorrules`, `.windsurfrules`,
`.clinerules`, `.roo/rules/mind.md`) kept in sync automatically.

## Quick Reference

| User intent | Action |
|---|---|
| "Remember that X" (project fact) | `python3 mind.py remember "X"` |
| "What/which/where ...?" (project fact not in context) | `python3 mind.py recall "the question"` |
| "X and Y are related" | `python3 mind.py link "X" "Y" "relation"` |
| "That's wrong, it's actually Z" | `python3 mind.py correct "old fact hint" "Z"` |
| "Clean up / consolidate memory" | `python3 mind.py dream --dry-run`, show the plan, then `python3 mind.py dream` if approved |
| "How is the memory doing?" | `python3 mind.py status` |

## How recall output looks

```
recall for "which database do we use" — 2 results [0.44 ms]

  1. [0.145] (direct) the project database is postgres 16
     (confidence 1.0, recalled 3x, weight 1.00)
  2. [0.043] (trace)  souq app uses prisma orm
```

`direct` = keyword/IDF match; `trace` = found through spreading activation
over linked memories. If recall returns nothing relevant, say so — do not
invent an answer.

## The dream cycle (between sessions)

`python3 mind.py dream` runs a deterministic sleep cycle: Ebbinghaus decay
prunes unused weak memories, weak edges are removed, clusters of related
memories are promoted to `.mind/cortex/`, contradictions are flagged (never
auto-deleted). Every action is explained in `.mind/dreams/<date>.md`.
Always offer `--dry-run` first when the user wants to see the plan.
It uses no LLM and costs zero tokens, so it is safe in Hermes' no-agent
cron mode (the script IS the job — no model call ever happens):

```bash
cat > ~/.hermes/scripts/mind_dream.sh <<'EOF'
#!/bin/bash
cd /path/to/project && python3 mind.py dream
EOF
hermes cron create "0 4 * * *" --name mind-dream \
  --script mind_dream.sh --no-agent
```

## Response style

- Confirm what was remembered/corrected in one short sentence.
- When recalling, quote the memory text and its confidence; never guess beyond it.
- After a dream, summarize the journal (pruned / promoted / flagged counts).

## Troubleshooting

- `no mind memory here` → run `python3 mind.py init` in the project root.
- Corrupt `graph.json` is quarantined automatically as `graph.json.corrupt-*`
  and memory restarts empty — tell the user where the quarantined file is.
- The tool refuses to write through symlinked agent files by design.

## Honest limits

- Lexical + graph recall (offline): cross-domain synonymy without corpus
  evidence can miss; benchmark and limitations are published in the README.
- Memory lifecycle is validated by a 180-day simulated-usage soak test in
  CI (core-fact survival 15/15 across daily/weekly/monthly cadences, stale
  junk eliminated 0/256); facts needed less often than ~every 6 weeks with
  fewer than 2 recalls decay into the archive by design.
- Per-project memory (hundreds to thousands of nodes), not enterprise RAG.
