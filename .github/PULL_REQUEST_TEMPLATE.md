## What & why

## Risk and compatibility

- Storage or format change:
- Security or process-boundary change:
- Backward-compatibility impact:
- Rollback or recovery path:

## Evidence

- Red reproduction or absence detector:
- Dynamic/adversarial result:
- Differential or unchanged-path result:
- Raw benchmark artifact, if applicable:

## Checklist
- [ ] `python3 tools/build_single.py --check` passes
- [ ] `python3 tools/claims.py check` passes
- [ ] `python3 tools/privacy_scan.py --tracked` passes
- [ ] `python3 -m unittest discover -s tests` passes in full
- [ ] behavior changes come with a regression test that fails before the fix
- [ ] storage changes cover crash, concurrency, old format, oversize, and privacy
- [ ] retrieval changes cover fallback, noisy, multilingual, and benchmark paths
- [ ] measured claims point to immutable committed JSON
- [ ] English and Arabic sections remain substantively equivalent
- [ ] zero new dependencies (the whole point of the tool)
- [ ] all review conversations are resolved
