# Security Policy

## Supported versions

Only the latest release is supported. `mind.py` is a single stdlib-only
file — updating is replacing one file.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** on this repository
(Security → Report a vulnerability), or open an issue for non-sensitive
hardening suggestions. You can expect an initial response within a few days.

## Security properties (and their tests)

- Atomic, durable writes on local filesystems: POSIX writes traverse and
  replace through opened directory handles, preserve existing permissions,
  use an unpredictable O_EXCL temporary file, check every byte, fsync before
  rename, and fsync the destination directory. Append-only logs
  (provenance journal, archive, dream journal, signals) use one checked
  O_NOFOLLOW+O_APPEND write, fsynced on the permanence-bearing ones.
  The lock file is opened O_NOFOLLOW (`tests/test_mind.py` — the regression
  classes `TestAuditFindings2`, `TestThirdAudit`, `TestFourthAudit`).
- Symlinks, FIFOs, devices, sockets, and multiply-linked files are refused
  on sensitive read/append paths. Agent files and export parents are skipped,
  never written through. Directory-handle traversal closes parent-swap races.
- Memory text is stripped of terminal control characters on every write
  and load path, including `correct`. Hot memories are collapsed to one
  data-labeled line and HTML guard markers are escaped before agent export.
- Hostile on-disk state is repaired, not trusted: a seeded fuzzer
  (`bench/fuzz.py`, 420 adversarial cases full / 160 quick in CI) holds
  the contract: no traceback; corrupt input is quarantined or repaired; the
  resulting graph loads clean and accepts a new write.
- The provenance journal (`journal.jsonl`) is append-only; both writes and
  reads require a private regular file. A short append is isolated from the
  next record. `why <id>` scans at most the latest 100 MB and retains at most
  10,000 matches; unfiltered status reads stay tail-bounded.
- One graph lock covers fresh reload, semantic decision, and one commit.
  The lock wait is bounded. Pruning uses a durable outbox so an interruption
  cannot create a false archive entry or lose an already-committed prune.
- Graph/query/history/cardinality limits bound memory and quadratic work.
  Dream candidate comparisons are budgeted, and consumed signals are removed
  by compare-and-rewrite prefix rather than unlinking concurrent suffixes.
- The memory is plain text and exported hot facts are agent-visible. Do not
  store secrets, credentials, private personal data, or untrusted prompt text.
- Node ids are `md5[:12]` content addresses — no security property is
  derived from them.
- No network access, no spawned processes, no eval — the file can be
  fully audited in one sitting (~3,000 lines). (`subprocess` is imported
  on Windows solely for its `list2cmdline` quoting helper; nothing is
  ever executed.)
