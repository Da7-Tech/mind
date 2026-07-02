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
  refused or skipped — never written through.
- Memory text is stripped of terminal control characters on write.
- No network access, no subprocess execution, no eval — the file can be
  fully audited in one sitting (~1,500 lines).
