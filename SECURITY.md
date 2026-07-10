# Security Policy

## Supported versions

Only the latest release is supported. `mind.py` is a single stdlib-only
file — updating is replacing one file.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** on this repository
(Security → Report a vulnerability), or open an issue for non-sensitive
hardening suggestions. You can expect an initial response within a few days.

## Security properties (and their tests)

- Atomic, durable writes: every replace-path write uses an unpredictable
  O_EXCL temporary file, checks that every byte was written, fsyncs before
  rename, and fsyncs the destination directory on POSIX. Append-only logs
  (provenance journal, archive, dream journal, signals) use one checked
  O_NOFOLLOW+O_APPEND write, fsynced on the permanence-bearing ones.
  The lock file is opened O_NOFOLLOW (`tests/test_mind.py` — the regression
  classes `TestAuditFindings2`, `TestThirdAudit`, `TestFourthAudit`).
- Symlinked agent files, lock files, archive files and export parents are
  refused or skipped — never written through. The lock file's parent chain
  is checked before creation, so even a symlinked `.mind/` root cannot
  cause a single file to be created outside the project (regression test:
  `test_symlinked_mind_root_refused_entirely`).
- Memory text is stripped of terminal control characters on every write
  and load path, including `correct`. Hot memories are collapsed to one
  data-labeled line and HTML guard markers are escaped before agent export.
- Hostile on-disk state is repaired, not trusted: a seeded fuzzer
  (`bench/fuzz.py`, 420 adversarial cases full / 160 quick in CI) holds
  the contract: no traceback; corrupt input is quarantined or repaired; the
  resulting graph loads clean and accepts a new write.
- The provenance journal (`journal.jsonl`) is append-only; both writes and
  reads refuse symlinks. `why <id>` streams the full file, while unfiltered
  status reads stay tail-bounded.
- Concurrent graph saves merge nodes per field and edges per directional
  pair; reinforcement is a delta, and once-per-day edge decay is decided
  while holding the graph lock.
- The memory is plain text and exported hot facts are agent-visible. Do not
  store secrets, credentials, private personal data, or untrusted prompt text.
- Node ids are `md5[:12]` content addresses — no security property is
  derived from them.
- No network access, subprocess execution, or eval — the file can be fully
  audited in one sitting (~2,900 lines).
