# mind

Local, deterministic memory for coding agents.

<!-- mind:section overview -->
## What It Is

`mind` stores durable project facts in a weighted concept graph, recalls them
through lexical ranking plus spreading activation, tracks where every fact
came from, ages unused salience, and consolidates recurring themes through a
deterministic dream cycle.

The default runtime is:

- local and plain-text;
- Python standard-library only;
- deterministic and offline;
- agent-neutral;
- distributed as one auditable `mind.py` file.

The development source is split by domain under `src/mind/`. A deterministic
builder reconstructs the single-file artifact byte-for-byte.

<!-- mind:section status -->
## Verified Status

<!-- mind:facts begin -->
- Development version: `7.0.0` (stable).
- Stable release: `7.0.0`; pinned `mind.py` SHA-256 `ae2fc389b3b09c93cb432ab55b71063d98b400da6b18d6bc178322bc8f3fcf69`.
- Discovered tests: **381**.
- Distribution: **10** source-domain fragments build one deterministic file; current artifact SHA-256 `ae2fc389b3b09c93cb432ab55b71063d98b400da6b18d6bc178322bc8f3fcf69`.
- CI matrix: **9** operating-system/Python cells.
- Command line: **30** commands; protocol server: **17** tools.
<!-- mind:facts end -->

`7.0.0` is the stable memory-platform release. It contains the lifecycle,
protocol-server, typed-memory, privacy, automatic-capture, and modular-source
features documented below.

<!-- mind:section install -->
## Install

### Stable release

```bash
curl -fsSLO https://raw.githubusercontent.com/Da7-Tech/mind/v7.0.0/mind.py
python3 -c "import hashlib; p=open('mind.py','rb').read(); assert hashlib.sha256(p).hexdigest() == 'ae2fc389b3b09c93cb432ab55b71063d98b400da6b18d6bc178322bc8f3fcf69'"
python3 mind.py init
```

### Development source

```bash
git clone https://github.com/Da7-Tech/mind.git
cd mind
python3 tools/build_single.py --check
python3 mind.py init
```

On stock Windows, exported commands use `py -3 mind.py`. A Windows CI field
test converts the artifact to CRLF, runs the exported invocation verbatim, and
checks the resulting project.

<!-- mind:section model -->
## Memory Model

| Layer | Storage | Role |
|---|---|---|
| Working memory | `.mind/ACTIVE.md` | bounded hot facts and operating contract |
| Hippocampus | `.mind/graph.json` | typed facts, validity, weights, relations |
| Provenance | `.mind/journal.jsonl` plus segments | append-only operation history |
| Cortex | `.mind/cortex/*.md` | recurring themes with owned guard blocks |
| Dreams | `.mind/dreams/*.md` | human-readable maintenance receipts |
| Scheduler | `.mind/scheduler.json` | bounded lease and pending-maintenance state |
| Pending queue | `.mind/pending.json` | quarantined automatic captures |

Facts can be semantic, episodic, procedural, or decisions. They also carry
scope, authority, source trust, sensitivity, expiration, pinning, and optional
`entity`/`attr` slots for contradiction detection.

<!-- mind:section automatic -->
## Auto-First Operation

`mind init` exports a guard-marked standing-order block into `AGENTS.md`,
`CLAUDE.md`, and `GEMINI.md`. Existing content outside the generated block is
preserved byte-for-byte. Existing Cursor, Windsurf, Cline, and Roo rule files
are adopted when present.

Agents use:

```bash
python3 mind.py capture "durable project fact"
```

Automatic capture:

- accepts stable project decisions, conventions, and environment facts;
- rejects credential and personal-identity patterns;
- rejects transient task state;
- quarantines untrusted material for review;
- infers conservative memory types and common contradiction slots;
- never copies project memory into the user-global tier.

Dream scheduling is independent from the telemetry log. A bounded scheduler
uses a lease, pending count, and recovery rules. Oversized telemetry resets
safely without disabling future maintenance.

Host integrations can consume machine-readable recipes:

```bash
python3 mind.py integrations --json
python3 mind.py context --json
```

The recipes cover session start, durable capture, pre-compaction batch flush,
session end, an optional scheduled backstop, and the protocol server.

<!-- mind:section commands -->
## Command Surface

```text
init
--help
--version [--verbose]
remember "text"
remember --user "text"
remember --json
remember --batch
capture "text" [--trust LEVEL]
pending
approve ID
reject ID
context [--json]
suggest-user [--json]
integrations [--json]
recall "question" [--at DATE] [--explain]
confirm ID [...]
correct "old hint" "new fact"
link "a" "b" [relation]
forget ID [--reason TEXT]
unlink A B
redact ID --reason TEXT
purge ID|--match TEXT --all-traces [--confirm]
why ID
entity "term"
dream [--dry-run]
backup [label]
checkpoint [label]
restore NAME [--confirm]
compact [--dry-run] [--keep-journal-days N]
merge BASE OURS THEIRS [--output PATH] [--graph-out PATH]
doctor [--bench] [--json]
growth [--days N] [--json]
export
status
mcp
```

Use `recall -- "-query beginning with a dash"` for a dash-leading query.

<!-- mind:section retrieval -->
## Recall And Explainability

Offline recall combines:

1. script-aware tokenization and normalization;
2. inverse-document-frequency direct matching;
3. related-term expansion;
4. bounded spreading activation over weighted relations;
5. reciprocal-rank fusion;
6. optional whole-ranking semantic reranking.

`recall --explain` prints direct, spread, fused, semantic, and final scores,
plus backend identity, process calls, latency, and fallback reason. Recall is a
pure read; useful hits change durability only after explicit `confirm`.

Directional relations such as `depends-on`, `owned-by`, and `deployed-to`
store truthful reverse labels while retaining bidirectional traversal.

<!-- mind:section semantic -->
## Optional Semantic Backends

The default remains offline. Two explicit optional protocols exist:

- `MIND_EMBED_CMD`: one versioned batch process per ranking;
- `MIND_EMBED_SERVER`: a persistent length-framed process with handshake,
  model revision, and dimension identity.

Both paths have bounded output, vector dimension, cache bytes, and total
ranking deadlines. A partial failure falls back the entire ranking to the
offline metric; similarity spaces are never mixed.

Reference server:

```bash
export MIND_EMBED_SERVER='python3 contrib/concept_embed_server.py'
python3 mind.py recall "where are backup copies kept" --explain
```

The process receives the query and candidate memory text. Only configure a
program you trust. The tool does not enforce that program's network isolation.

<!-- mind:section protocol -->
## Agent Protocol Server

```bash
python3 mind.py mcp
```

The same file serves newline-delimited JSON-RPC over standard input/output.
It supports initialization, ping, tool listing/calls, cancellation
notifications, clean EOF shutdown, and seventeen memory, diagnostic, and
privacy tools. Standard output contains protocol JSON only.

Minimal lifecycle:

```text
initialize
notifications/initialized
tools/list
tools/call
notifications/cancelled
```

<!-- mind:section storage -->
## Storage Lifecycle

`status` separates current journal bytes from segment count and segment bytes.
`compact` rotates an oversized active archive, segments a current journal when
it exceeds budget or is wholly older than the requested retention horizon, and
collects stale temporary files.

Backups are plain files with a SHA-256 manifest:

```bash
python3 mind.py backup before-upgrade
python3 mind.py restore BACKUP_NAME
python3 mind.py restore BACKUP_NAME --confirm
```

A confirmed restore creates a pre-restore checkpoint first. Privacy rewrites
also refresh backup manifests so a remediated backup remains verifiable.
Restore writes an exact file plan, removes later managed files, and resumes an
interrupted plan before normal memory loading.

<!-- mind:section privacy -->
## Privacy Lifecycle

- `forget` removes a fact from retrieval but keeps an auditable tombstone.
- `unlink` removes a relation without deleting its endpoints.
- `redact` replaces payloads with a digest and reason across managed stores.
- `purge` inventories first; `--confirm` irreversibly removes payload and node
  identifiers from graph, journals, archives, dreams, cortex, exports, queues,
  receipts, and backups.

Redaction and purge use a crash-resumable outbox. Exact-byte tests search every
managed artifact after completion. Secrets should still never be stored:
remediation is a last resort, not a secret manager.

<!-- mind:section merge -->
## Git-Mergeable Memory

Journal format v2 adds UTC epoch-nanosecond time and stable event IDs. The
three-way merge command deduplicates suffix events, orders them
deterministically, and can replay the merged journal into a graph:

```bash
python3 mind.py merge BASE OURS THEIRS --output MERGED --graph-out GRAPH
```

Example merge driver:

```ini
[merge "mind-journal"]
    name = deterministic mind journal merge
    driver = python3 mind.py merge %O %A %B --output %A
```

```gitattributes
.mind/journal.jsonl merge=mind-journal
```

On Windows, replace `python3` with `py -3`.

<!-- mind:section diagnostics -->
## Diagnostics And Felt Growth

```bash
python3 mind.py doctor --bench
python3 mind.py growth --days 30
python3 mind.py suggest-user
```

`doctor` checks storage boundaries, recovery outboxes, scheduler leases,
duplicate export guards, BOM/CRLF handling, stale temporary files, backend
configuration, and clock anomalies. Its optional personal benchmark appends a
local recall history.

`growth` derives learned, confirmed, corrected, forgotten, dreamed, promoted,
and conflict counts from journal and dream truth. The latest bounded
consolidation receipt is visible in every generated `ACTIVE.md`.

<!-- mind:section benchmarks -->
## Reproducible Evidence

Every public result is JSON tied to an immutable input, source identity,
backend identity, and exact command.

<!-- mind:benchmarks begin -->
| Result | Current evidence | Raw report |
|---|---:|---|
| BM25 baseline | 0.660 / 0.920 / 0.560 | [`longmemeval-bm25-v7-dev.json`](bench/results/longmemeval-bm25-v7-dev.json) |
| mind offline | 0.500 / 0.840 / 0.580 | [`longmemeval-offline-v7-dev.json`](bench/results/longmemeval-offline-v7-dev.json) |
| mind with concept sidecar | 0.560 / 0.840 / 0.520 | [`longmemeval-concept-v7-dev.json`](bench/results/longmemeval-concept-v7-dev.json) |
| Paraphrase traps | offline 0/20; sidecar 20/20 | [`paraphrase-v7-dev.json`](bench/results/paraphrase-v7-dev.json) |
| 10,000-fact bulk ingest | one commit; conservative 91.0x speedup | [`bulk-v7-dev.json`](bench/results/bulk-v7-dev.json) |
| Auto-first horizon | 30 sessions and 1825 simulated days | [`autonomy-five-year-v7-dev.json`](bench/results/autonomy-five-year-v7-dev.json) |
| Single-file mutations | 43/120 killed (35.8%); 77 survived | [`mutation-mind-v7-dev.json`](bench/results/mutation-mind-v7-dev.json) |
| LongMemEval-harness mutations | 35/120 killed (29.2%); 85 survived | [`mutation-longmemeval-v7-dev.json`](bench/results/mutation-longmemeval-v7-dev.json) |

On this subset, BM25 leads both evidence metrics. This benchmark does not measure graph traversal, temporal validity, contradiction handling, or lifecycle operations, so it does not establish overall product superiority.

LongMemEval values are evidence@1 / evidence@5 / answer-string@5.
<!-- mind:benchmarks end -->

Raw files live under `bench/results/`. The LongMemEval input is pinned by
revision and SHA-256 in `bench/manifests/longmemeval.json`.

LongMemEval memory records include metadata prefixes such as date, question
ID, session ID, and role. These prefixes improve provenance and isolation but
also add query-independent tokens; evidence metrics therefore use exact
node labels, and answer-string metrics are reported separately.

Mutation analysis performs a green product-suite baseline preflight, stages
every product-test dependency, mutates modular source and artifact
consistently, preserves bounded diagnostics, and distinguishes killed,
survived, timed out, compile error, and infrastructure error. The three
self-referential public-evidence tests run after report generation because
they validate the completed report itself; every report records this exclusion
and the exact baseline test count.

<!-- mind:section limits -->
## Boundaries And Non-Goals

- Maximum graph: 10,000 nodes, 100,000 directional edges, 50 MB.
- Maximum fact/query: 10,000 characters.
- Active archive rotates at 8 MB.
- Signals reset at 5 MB; scheduling survives independently.
- The default is project memory, not document-scale RAG.
- Dreaming is consolidation, not rollback.
- A pruned fact is archived; a pruned edge is not restorable automatically.
- Automatic capture depends on a host following the exported contract or
  calling the protocol/hooks. No tool can force a host that ignores both.

<!-- mind:section development -->
## Development

### Environment

| Variable | Purpose |
|---|---|
| `MIND_AUTO_DREAM` | Set to `0`, `false`, or `no` to disable write-triggered maintenance. |
| `MIND_BY` | Bounded provenance actor supplied by a host integration. |
| `MIND_SESSION` | Bounded provenance session supplied by a host integration. |
| `MIND_USER_HOME` | Explicit user-tier directory; defaults to `~/.mind`. |
| `MIND_EMBED_CMD` | Optional one-process batch semantic command. |
| `MIND_EMBED_SERVER` | Optional persistent framed semantic server. |
| `MIND_EMBED_TIMEOUT` | Per-operation semantic timeout. |
| `MIND_EMBED_BUDGET` | Total semantic ranking deadline. |
| `MIND_LOCK_TIMEOUT_SECONDS` | Cross-process graph-lock deadline. |
| `MIND_DEBUG` | Print tracebacks for command failures. |

```bash
python3 tools/build_single.py --check
python3 tools/claims.py check
python3 -m unittest discover -s tests -v
python3 bench/bench.py
python3 bench/multilang.py
python3 bench/discrim.py
python3 bench/slots.py
python3 bench/soak.py
python3 bench/fuzz.py --quick
python3 bench/autonomy.py --quick
```

Release-only gates also run the full five-year autonomy horizon, immutable
LongMemEval subset, both mutation targets, privacy scan, and all nine CI cells.
See the [V7 verification record](docs/VERIFICATION.md) for the three-method
gate, raw evidence, limitations, and remote completion requirements.

<!-- mind:section security -->
## Security

Read [SECURITY.md](SECURITY.md) before enabling an external semantic backend
or storing sensitive project material. The default kernel does not use the
network or child processes. Optional semantic modes execute a trusted local
program and pass memory text across that process boundary.

MIT licensed.
