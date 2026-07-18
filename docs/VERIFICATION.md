# V7 Verification Record

This record defines the evidence required before the V7 development branch can
be merged. Passing one methodology never substitutes for another.

## Method One: Static and Structural

The static gate verifies source structure, generated artifacts, documented
surfaces, immutable inputs, raw-result provenance, and privacy.

```sh
python3 -m py_compile mind.py src/mind/*.py bench/*.py tools/*.py
python3 tools/build_single.py --check
python3 tools/claims.py check
python3 tools/privacy_scan.py
git diff --check
```

Required invariants:

- the modular source builds byte-for-byte into `mind.py`;
- every documented command, flag, environment variable, protocol tool, test
  count, version, checksum, and result link matches executable state;
- every public result comes from a clean tree and carries the current
  `mind.py` digest;
- mutation reports use the current corpus, a green product-suite baseline,
  complete classification, and isolated confirmation for every parallel kill;
  the three self-referential claims tests run after report generation, and the
  report records both this exclusion and the exact baseline test count;
- English and Arabic documentation expose the same marked sections;
- no private path, email address, secret pattern, private audit, or local task
  ledger enters the public tree.

## Method Two: Dynamic and Adversarial

The dynamic gate exercises normal behavior, boundary crossings, corruption,
crashes, concurrency, privacy erasure, protocol handling, and long-horizon
autonomy.

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -q
python3 bench/bench.py
python3 bench/multilang.py
python3 bench/discrim.py
python3 bench/slots.py
python3 bench/soak.py
python3 bench/fuzz.py --quick
python3 bench/autonomy.py --days 1825 --payload-chars 5000
```

The test suite includes a sparse 100 MB archive crossing, CRLF and BOM foreign
writers, exact concurrent scheduler updates, one-winner leases, crash-resumable
privacy operations, exact restore, tampered backup rejection, persistent
semantic-server failure handling, malformed protocol messages, and deterministic
journal-merge convergence.

## Method Three: Differential and Metamorphic

The differential gate proves equivalence across representations and degraded
conditions rather than merely checking one happy path.

```sh
python3 -m unittest -q \
  tests.test_distribution \
  tests.test_round14 \
  tests.test_merge \
  tests.test_storage \
  tests.test_scheduler
```

Required comparisons:

- modular source versus generated one-file distribution;
- offline ranking versus whole-ranking fallback after any semantic failure;
- original export versus CRLF, BOM, and mixed-line-ending transformations;
- both branch orders and randomized interleavings for journal merge;
- backup digest versus restored digest, including interrupted restore recovery;
- explicit empty protocol input versus end-of-file behavior;
- current format versus prior-format fixtures.

## Public Evidence

| Area | Raw result |
|---|---|
| LongMemEval, offline | [`longmemeval-offline-v7-dev.json`](../bench/results/longmemeval-offline-v7-dev.json) |
| LongMemEval, BM25 | [`longmemeval-bm25-v7-dev.json`](../bench/results/longmemeval-bm25-v7-dev.json) |
| LongMemEval, concept sidecar | [`longmemeval-concept-v7-dev.json`](../bench/results/longmemeval-concept-v7-dev.json) |
| Paraphrase traps | [`paraphrase-v7-dev.json`](../bench/results/paraphrase-v7-dev.json) |
| 10,000-fact bulk ingest | [`bulk-v7-dev.json`](../bench/results/bulk-v7-dev.json) |
| Five-year autonomy | [`autonomy-five-year-v7-dev.json`](../bench/results/autonomy-five-year-v7-dev.json) |
| One-file mutations | [`mutation-mind-v7-dev.json`](../bench/results/mutation-mind-v7-dev.json) |
| LongMemEval mutations | [`mutation-longmemeval-v7-dev.json`](../bench/results/mutation-longmemeval-v7-dev.json) |

The 50-question LongMemEval subset is deliberately reported as a subset. BM25
leads both evidence metrics on it. The benchmark does not measure graph
traversal, temporal validity, contradiction handling, privacy lifecycle, merge,
or autonomous consolidation, and therefore is not an overall product ranking.

Mutation rates are test-sensitivity measurements, not product-quality scores.
Every candidate kill produced under parallel execution is rerun in isolation;
parallel-only failures are reclassified and retained as diagnostic evidence.

## Traceability

| Scope | Primary implementation evidence | Primary test evidence |
|---|---|---|
| Availability and integrity, C-01 through C-05 | filesystem, graph, scheduler, cortex, dream domains | `test_round14`, `test_scheduler`, `test_mind` |
| Retrieval and benchmark trust, C-06 through C-15 | embedding, recall, benchmark, claims tooling | `test_longmemeval_bench`, `test_mutation_bench`, `test_claims` |
| Semantics and privacy, C-16 through C-22 | graph metadata, lifecycle manager, storage commands | `test_lifecycle`, `test_storage`, `test_typed_memory` |
| Governance and distribution, C-23 through C-26 | workflows, ownership, release dry run, deterministic build | `test_distribution`, remote required checks |
| Portability and hygiene, C-27 through C-34 | invocation, cache bounds, parser, conflict provenance | `test_round14`, `test_mind`, `test_provenance` |
| Roadmap A1 through A14 | protocol, bulk ingest, sidecar, tiers, merge, doctor, policy, scoreboard | domain test modules and all eight raw results |
| Auto-first contract | policy capture, scheduler lease, growth receipts, five-year lifecycle | `test_policy`, `test_scheduler`, autonomy result |

## Remote Completion Gate

Local evidence is necessary but not sufficient. Completion also requires:

- all nine operating-system and Python matrix cells green on the pushed commit;
- the release dry-run workflow green;
- required review and conversation controls verified through repository state;
- no open code-scanning, dependency, or secret-scanning finding attributable
  to the change;
- the merged commit reachable from the default branch and a clean local
  worktree.
