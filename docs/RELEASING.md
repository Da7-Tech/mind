# Release Protocol

Releases are cut from a clean, reviewed commit. The one-file artifact, source,
documentation, raw evidence, and tag must all identify the same state.

## Freeze

1. Stop feature work and choose the release commit.
2. Update `CHANGELOG.md` and replace the development version.
3. Build and verify the distribution:

   ```bash
   python3 tools/build_single.py
   python3 tools/build_single.py --check
   ```

4. Run the complete test and adversarial suites on the built artifact.
5. Generate every public benchmark from the clean release commit.
6. Run:

   ```bash
   python3 tools/claims.py update
   python3 tools/claims.py check
   python3 tools/privacy_scan.py --tracked
   ```

## Publish

1. Merge only after required checks and conversations are resolved.
2. Create a signed annotated tag:

   ```bash
   git tag -s vX.Y.Z -m "mind vX.Y.Z"
   git push origin vX.Y.Z
   ```

3. Verify the remote tag object, commit, artifact checksum, and README install
   command.
4. Publish no metric unless its raw JSON is committed under `bench/results/`.

## Resume Development

Immediately bump the default branch to the next distinct development version.
Two byte-different artifacts must never report the same bare version.

## Solo-Maintainer Tradeoff

Code ownership, required checks, conversation resolution, signed tags, and
review requests remain useful in a solo repository, but a second approval
cannot be guaranteed. Any administrator bypass is an explicit governance
event, not evidence of independent review. Storage, privacy, protocol, and
release changes should obtain an outside review whenever a reviewer is
available.
