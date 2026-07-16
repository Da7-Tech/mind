# Contributing

## Invariants

1. The default kernel is deterministic, offline, and standard-library only.
2. `src/mind/` is the source of truth. Never hand-edit generated `mind.py`.
3. `python3 tools/build_single.py` must reconstruct `mind.py` byte-for-byte.
4. Plain files remain the default storage, and current plus previous formats
   remain readable.
5. Recall stays a pure read. Durable writes are atomic, serialized,
   recoverable, and journaled.

## Change Protocols

Every behavior change needs an absence detector or red regression test.

Storage changes must cover:

- every durable crash point;
- concurrent writers;
- the previous format;
- below, at, and beyond each size boundary;
- backup, restore, compaction, redaction, and purge behavior.

Retrieval changes must cover:

- clean and noisy corpora;
- competing vocabulary and near duplicates;
- multilingual and temporal cases;
- empty/no-result precision;
- semantic failure and whole-ranking fallback;
- immutable LongMemEval evidence.

Every bounded constant needs a crossing test and a lifecycle policy. Every
quality harness needs a green baseline preflight and a planted-defect
self-test.

## Required Local Gates

```bash
python3 tools/build_single.py --check
python3 tools/claims.py check
python3 tools/privacy_scan.py --tracked
python3 -m unittest discover -s tests -v
python3 bench/bench.py
python3 bench/multilang.py
python3 bench/discrim.py
python3 bench/slots.py
python3 bench/soak.py
python3 bench/fuzz.py --quick
python3 bench/autonomy.py --quick
```

Performance claims require immutable input, source commit, raw JSON, and a
README summary generated from that JSON. Do not edit generated facts or
benchmark tables manually.

## Review

Storage, privacy, semantic-process, protocol, workflow, and release changes
request code-owner review. Resolve every review conversation. In a
solo-maintainer repository, an administrator bypass is explicit governance
debt and is not independent review.

Scoped work lives in the
[issues](https://github.com/Da7-Tech/mind/issues). Use a Discussion when the
format, compatibility, or product boundary is still uncertain.
