# mind Design

## Invariants

1. Offline deterministic behavior is the default.
2. The default runtime is standard-library only.
3. Distribution remains one auditable Python file.
4. Development source is modular and builds that file deterministically.
5. Recall is a pure read.
6. Durable mutations are serialized, atomic, recoverable, and journaled.
7. Current and immediately previous storage formats remain readable.
8. Optional semantic acceleration cannot mix ranking spaces.
9. Project memory never enters the user-global tier silently.
10. Generated agent content has explicit ownership boundaries.

## Source And Distribution

`src/mind/source.json` orders ten domain fragments:

- prelude/filesystem;
- language and lexical retrieval;
- optional embedding protocols;
- graph and provenance;
- cortex;
- dreams;
- export and portable invocation;
- policy and pending queue;
- lifecycle, storage, merge, doctor, and growth;
- command line and protocol server.

`tools/build_single.py` compiles every fragment, concatenates exact bytes, and
either writes or verifies `mind.py`. Tests import both modular source and the
artifact and compare behavior.

## Transaction Model

Every public graph mutation:

1. acquires the per-object thread lock;
2. acquires the cross-process graph lock;
3. reloads the newest graph;
4. makes the semantic decision;
5. commits the graph once;
6. appends one durable journal batch;
7. appends one telemetry batch.

Direct in-memory edits are unsupported public behavior. Internal tests that
construct a state explicitly call `_save()` before invoking a public
operation. This removed legacy mutation trackers and repeated full-graph
digests.

## Recall

Direct retrieval uses IDF-scored keys. Related-term expansion and bounded
spreading activation add graph evidence. Reciprocal-rank fusion stabilizes
heterogeneous channels. The optional semantic stage reranks only the bounded
head.

One ranking elects one backend:

- the persistent server is attempted first when explicitly configured;
- otherwise one batch command is attempted;
- every vector must share model identity and dimension;
- any failure selects offline scoring for the entire ranking.

Receipts retain direct, spread, fused, semantic, final, backend, call, latency,
and fallback information.

## Storage And Provenance

Graph format 2 stores typed metadata and directed relation fields. Journal
format 2 stores compatible local ISO time, UTC epoch nanoseconds, and stable
event IDs.

Current journals segment as whole append-only files. Segment creation records
the segment digest in the new current journal. Reads present current and
segments as one logical log. Active archives rotate in constant time at their
budget.

Backups are plain files under `.mind/backups/` with a SHA-256 manifest. Restore
is dry-run by default, creates a pre-restore checkpoint, executes an exact
write/delete plan, and resumes an interrupted plan before graph loading.

## Automatic Maintenance

Telemetry and scheduling are separate:

- `signals.jsonl` is bounded observational data;
- `scheduler.json` is bounded authoritative state;
- a lease prevents overlapping dream cycles;
- a claimed pending count is subtracted only after successful completion;
- oversized or unreadable telemetry resets without disabling scheduling.

Dream inputs are graph weights and metadata, not replayed signal text.

## Privacy

Automatic policy rejects secrets and identity-like or transient data. Typed
metadata controls expiry, pinning, trust, sensitivity, promotion, and
slot-aware conflicts.

Forget is a retrieval tombstone. Redact and purge use a lifecycle outbox that
rewrites managed stores one path at a time and resumes after interruption.
Backup manifests are recalculated after privacy rewrites.

## Merge

Three-way journal merge accepts an explicit common ancestor. Events already in
the base are excluded from branch suffixes. Suffix events deduplicate by stable
ID and sort by UTC time, actor, and event ID. Replay recomputes graph weights
from operations, so counters add exactly and floats are not snapshot-merged.

## Automation Surfaces

The generated agent contract is the broad fallback. Structured integrations
are available through `context --json`, `integrations --json`, JSONL bulk
ingest, and the standard-input/output protocol server.

No mechanism can force a host that ignores both instruction files and
integration calls. Documentation states this boundary explicitly.

## Verification Pyramid

- focused unit and regression tests;
- source/artifact differential tests;
- concurrency and crash injection;
- foreign-writer and line-ending tests;
- multilingual, discrimination, fuzz, and soak suites;
- immutable LongMemEval and paraphrase evidence;
- mutation self-tests and structured mutation reports;
- thirty-session and five-year autonomy simulation;
- nine operating-system/Python CI cells;
- claims and privacy gates before publication.
