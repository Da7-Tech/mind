---
name: mind
description: Per-project memory graph with recall, decay, and dreams.
version: 5.4.1
author: Da7-Tech
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  commands: [python3, curl]
related_skills: [hermes-agent]
metadata:
  hermes:
    tags: [Memory, Knowledge-Graph, Consolidation, Offline, Local-First]
    category: autonomous-ai-agents
    homepage: https://github.com/Da7-Tech/mind
---

# mind Skill

Gives a project a persistent, self-organizing memory: a weighted concept
graph in `.mind/` with spreading-activation recall, Ebbinghaus forgetting,
and a deterministic dream cycle — exported into `AGENTS.md`/`CLAUDE.md`/
`GEMINI.md` behind guard markers. It complements Hermes' built-in memory
(small, curated, *global* user facts) with *per-project* knowledge. It does
NOT store durable personal facts about the user — those belong in the
built-in `memory` tool — and it is not a RAG system for large corpora.

## When to Use

- The user asks to remember project facts, decisions, or context across sessions
- A project fact is needed that is not in context ("what database do we use?")
- The user corrects a stored fact
- Between-session housekeeping ("clean up / consolidate the project memory")

## Prerequisites

- `python3` (3.9+) and `curl` on PATH — nothing else: no API keys, no
  server, no packages. The tool is one stdlib-only file, MIT-licensed,
  from https://github.com/Da7-Tech/mind (88 tests + benchmark + 180-day
  soak test run in its CI on Linux/macOS/Windows).

## How to Run

Install once per project through the `terminal` tool, pinned to a release
tag and integrity-checked:

```bash
cd <project>
curl -fsSLO https://raw.githubusercontent.com/Da7-Tech/mind/v5.4.1/mind.py
echo "642fb8e4c445ef4fe6e7739e07547d704015622209cb759094f61d9ece059b35  mind.py" | shasum -a 256 -c
python3 mind.py init
```

`init` creates `.mind/` and writes guard-marked memory blocks into
`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`; existing user content is preserved
outside the markers. Projects already using Cursor/Windsurf/Cline/Roo get
their rule files synced too (adopted only when present).

## Quick Reference

| User intent | Command (through `terminal`) |
|---|---|
| "Remember that X" (project fact) | `python3 mind.py remember "X"` |
| Project fact not in context | `python3 mind.py recall "the question"` |
| A recalled memory actually answered | `python3 mind.py confirm <id>` (ids in recall output) |
| "X and Y are related" | `python3 mind.py link "X" "Y" "relation"` |
| "That's wrong, it's actually Z" | `python3 mind.py correct "old fact hint" "Z"` |
| "Clean up memory" | `python3 mind.py dream --dry-run`, show plan, then `dream` |
| Health report | `python3 mind.py status` |

## Procedure

1. On a recall request, run `recall` and quote the memory text with its
   confidence. If nothing relevant returns, say so — never invent.
2. When a recalled memory actually answered the question, run
   `confirm <id>` — confirmed memories harden (+2 weeks stability) and
   their edges restrengthen; unconfirmed ones decay and get pruned
   (into `.mind/archive.md`, never destroyed).
3. For corrections use `correct` (old text is kept in node history).
4. For housekeeping, always run `dream --dry-run` first and show the plan;
   apply only on approval. Every dream action is explained in
   `.mind/dreams/<date>.md`.
5. Nightly automation costs zero tokens via the `cronjob` tool in no-agent
   mode (the script IS the job — no model call). Use the `write_file` tool
   to create `~/.hermes/scripts/mind_dream.sh` with this body:

   ```
   #!/bin/sh
   cd /path/to/project && python3 mind.py dream
   ```

   then register it (POSIX cron; on Windows use Task Scheduler to run the
   same `python3 mind.py dream` daily):

   ```
   hermes cron create "0 4 * * *" --name mind-dream --script mind_dream.sh --no-agent
   ```

## Pitfalls

- Recall is lexical + graph-structural (offline): cross-domain synonymy
  with no corpus evidence can miss; benchmark and limits are published in
  the repo README.
- Facts recalled fewer than twice and untouched past the 45-day grace
  window decay into `.mind/archive.md` by design (restorable with
  `remember`).
- Corrupt `graph.json` is quarantined as `graph.json.corrupt-*` and memory
  restarts empty — tell the user where the quarantined file is.
- The tool refuses to write through symlinked agent/lock/archive files.

## Verification

```bash
cd "$(mktemp -d)" && curl -fsSLO https://raw.githubusercontent.com/Da7-Tech/mind/v5.4.1/mind.py && python3 mind.py init >/dev/null && python3 mind.py remember "the sky signal is 7413" >/dev/null && python3 mind.py recall "sky signal"
```

Expected: one result containing `7413` with a printed memory id.
