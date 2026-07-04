---
name: mind
description: Project memory graph with recall, provenance, and dreams.
version: 6.1.1
author: Da7 (Da7-Tech)
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
  from https://github.com/Da7-Tech/mind (161 tests + benchmarks incl. 10 languages + discrimination + fuzzer + 180-day
  soak test run in its CI on Linux/macOS/Windows).

## How to Run

Install once per project through the `terminal` tool, pinned to a release
tag and integrity-checked:

```bash
cd <project>
curl -fsSLO https://raw.githubusercontent.com/Da7-Tech/mind/v6.1.1/mind.py
python3 -c "import hashlib;h=hashlib.sha256(open('mind.py','rb').read()).hexdigest();assert h=='8d58eae04e422b24cb5e80fb9322008ed408df9e6b09b7b8076a50c0adb5101b',h;print('mind.py: OK')"
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
| "Where did this fact come from?" | `python3 mind.py why <id>` |
| "What do we know about X?" | `python3 mind.py entity "X"` |
| "What was true on DATE?" | `python3 mind.py recall "q" --at YYYY-MM-DD` |
| "Clean up memory" / daily or ≥10 pending signals | `python3 mind.py dream` (no permission needed) |
| Health report | `python3 mind.py status` |

## Procedure

1. On a recall request, run `recall` and quote the memory text with its
   confidence. If nothing relevant returns, say so — never invent.
2. When a recalled memory actually answered the question, run
   `confirm <id>` — confirmed memories harden (+2 weeks stability) and
   their edges restrengthen; unconfirmed ones decay and get pruned
   (into `.mind/archive.md`, never destroyed).
3. For corrections use `correct` — the wrong fact is CLOSED (not erased):
   its validity ends now, a `supersedes` edge records the transition, and
   `why <id>` / `recall --at` can still reach it. Never re-`remember` a
   wrong fact to "overwrite" it — that reopens it.
4. Provenance is automatic (append-only `.mind/journal.jsonl`, never
   cleared). Set `MIND_BY` and `MIND_SESSION` env vars when running
   commands so `why` can attribute facts to you/this session.
5. Consolidation cadence: run `dream` once per day (no journal for
   today in `.mind/dreams/`) AND whenever `status` shows ≥ 10 pending
   signals — without asking. It is deterministic and reversible
   (archive, never delete); use `--dry-run` first only when the user
   explicitly asks to review the plan. Every action is explained in
   `.mind/dreams/<date>.md`.
6. Nightly automation costs zero tokens via the `cronjob` tool in no-agent
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
cd "$(mktemp -d)" && curl -fsSLO https://raw.githubusercontent.com/Da7-Tech/mind/v6.1.1/mind.py && python3 -c "import hashlib;h=hashlib.sha256(open('mind.py','rb').read()).hexdigest();assert h=='8d58eae04e422b24cb5e80fb9322008ed408df9e6b09b7b8076a50c0adb5101b',h;print('OK')" && python3 mind.py init >/dev/null && python3 mind.py remember "the sky signal is 7413" >/dev/null && python3 mind.py recall "sky signal"
```

Expected: one result containing `7413` with a printed memory id.
