# Security Policy

## Supported Versions

Only published releases explicitly listed as supported receive security
updates. Development previews are for evaluation and must not be confused with
the pinned stable artifact.

## Reporting

Use GitHub private vulnerability reporting for sensitive issues. Use a public
issue only for non-sensitive hardening suggestions. Do not paste credentials,
private memory graphs, journals, or unredacted agent files into an issue.

## Threat Model

`mind` protects local project memory from accidental corruption, concurrent
writers, common filesystem redirection attacks, malformed local state, and
untrusted text entering active agent instructions.

It does not protect a host account already controlled by an attacker, provide
encrypted storage, isolate an optional external embedding program, or turn
plain-text project memory into a secret store.

## Default Offline Kernel

Without optional semantic configuration:

- runtime code uses the Python standard library only;
- recall, storage, dreams, exports, merge, and diagnostics require no network;
- no child process is launched for retrieval;
- memory remains in local plain-text files;
- deterministic behavior does not depend on a remote model.

The source is developed in domain fragments and built into one deterministic
artifact. `python3 tools/build_single.py --check` proves byte equality.

## Optional Process Boundary

`MIND_EMBED_CMD` and `MIND_EMBED_SERVER` execute a user-configured local
program. Query text and candidate memory text are sent to that process.

Controls:

- commands are parsed without a shell;
- relative programs resolve at configuration time;
- output size, vector dimension, cache bytes, and total deadline are bounded;
- processes use a separate group and are terminated on deadline;
- persistent servers must pass a protocol/model/revision/dimension handshake;
- partial failure falls back the entire ranking;
- benchmark `--require-embed` converts fallback into a failure.

Residual boundary:

- the configured program may use the network;
- the tool does not sandbox it;
- repository-controlled environment files or hooks must never set an
  embedding command without explicit trust;
- backend stderr is not treated as trusted data.

## Automatic Capture Boundary

Automatic capture rejects recognizable credentials, personal identity,
transient task state, and untrusted source material. Untrusted material is
quarantined. Hot memories are escaped and labeled as data before export.

This is defense in depth, not a complete secret detector. Users and agents must
still avoid storing credentials or private personal material.

## Filesystem And Concurrency Properties

- Sensitive reads and writes reject symlinks and unsafe file types.
- Atomic writes use private temporary files, checked full writes, fsync, and
  replacement under a verified boundary.
- Append-only durability paths use checked regular-file appends.
- One graph lock covers fresh reload, semantic decision, and one graph commit.
- Scheduler and pending queues use independent bounded locks.
- Archive prune and privacy lifecycle operations use crash-recoverable
  outboxes.
- CRLF, BOM, and foreign editor rewrites cannot duplicate the generated agent
  contract.
- Cortex ownership guards preserve manual content outside generated material.
- Backups use per-file SHA-256 manifests; restore verifies every file and
  creates a checkpoint first.

## Privacy Remediation

`forget` is reversible at the data layer because provenance remains.
`redact` replaces payloads with a digest receipt. `purge --confirm` is
irreversible and searches every managed live artifact, journal segment,
archive, export, queue, receipt, and backup.

Privacy remediation refreshes backup manifests after rewriting backup content.
An outbox resumes interrupted work before normal operation continues.

## Limits And Denial Of Service

Facts, queries, graph bytes, node/edge counts, history, journal scans,
comparison work, signals, pending queues, caches, vector dimensions, and
diagnostic output are bounded. Active archives and journals segment or rotate.

Very large but valid projects can still consume CPU and disk near configured
limits. `status`, `doctor`, and `compact --dry-run` expose approaching
boundaries.

## Content IDs

Legacy node IDs use the first twelve hexadecimal characters of MD5 for stable
content addressing. Calls explicitly mark MD5 as non-security use for
FIPS-enforcing Python. No authentication, authorization, or integrity decision
depends on that ID.

## Verification

Security-relevant changes require:

- regression tests for the reproduced failure;
- crash, concurrency, compatibility, and boundary checks where applicable;
- full test and adversarial suites;
- deterministic artifact verification;
- claims/documentation check;
- public-diff privacy scan;
- review of optional process and data-boundary changes.
