# Contributing

Ground rules (they keep the tool what it is):

1. **`mind.py` stays a single stdlib-only file.** No dependencies, no
   network calls in the default path. Helper tooling may live in
   `bench/`/`tests/` and use whatever it needs.
2. **Every behavior change needs a test** (`tests/`, stdlib `unittest`):
   `python3 -m unittest discover -s tests`
3. **Every performance or lifecycle claim needs a measurement**:
   `python3 bench/bench.py` and `python3 bench/soak.py` must stay green —
   both run in CI on Linux/macOS/Windows × 3 Python versions.
4. Scoped, ready-to-pick-up work lives in the
   [issues](https://github.com/Da7-Tech/mind/issues).

If you're unsure, open a Discussion first — happy to scope things together.
