# Security Policy

## Supported versions

Only the latest release is supported. `mind.py` is a single stdlib-only
file — updating is replacing one file.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** on this repository
(Security → Report a vulnerability), or open an issue for non-sensitive
hardening suggestions. You can expect an initial response within a few days.

## Security properties (and their tests)

- Atomic, durable writes: O_NOFOLLOW + fsync-before-rename everywhere,
  including the lock file (`tests/test_mind.py::TestAuditFindings`).
- Symlinked agent files, lock files, archive files and export parents are
  refused or skipped — never written through. The lock file's parent chain
  is checked before creation, so even a symlinked `.mind/` root cannot
  cause a single file to be created outside the project (regression test:
  `test_symlinked_mind_root_refused_entirely`).
- Memory text is stripped of terminal control characters on every write
  path, including `correct`.
- Hostile on-disk state is repaired, not trusted: a seeded fuzzer
  (`bench/fuzz.py`, 420 adversarial cases, runs in CI) holds the contract
  "no traceback, no data loss, graph always loads clean" against corrupt
  graph files and hostile CLI input.
- The provenance journal (`journal.jsonl`) is append-only and written
  behind the same symlink/parent-boundary checks as every other file.
- No network access, no subprocess execution, no eval — the file can be
  fully audited in one sitting (~2,100 lines).
