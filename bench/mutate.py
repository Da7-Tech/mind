#!/usr/bin/env python3
"""mind mutation tester — does the test suite actually bite?

Generates first-order mutants of mind.py (AST-level, deterministic seed),
runs the full unit suite against each, and reports the kill rate. A mutant
"survives" when every test still passes with the defect injected — each
survivor is either a test-suite gap worth closing or an equivalent mutant
worth documenting. Zero dependencies (ast + subprocess).

Mutation operators:
  - comparison flips:  <  <->  <= ,  >  <->  >= ,  ==  <->  !=
  - boolean flips:     and <-> or
  - arithmetic flips:  +  <->  - ,  *  <->  /
  - numeric constant nudges:  n -> n + 1  (ints),  x -> x * 2  (floats)

Run:  python3 bench/mutate.py [--sample N]   (default: 120 mutants)

This is a release-gate analysis tool, not a CI gate: a full run costs
minutes, and kill-rate targets belong in a human's judgment, not a
pass/fail bit. Survivors are printed with line numbers for triage.
"""
import ast
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

random.seed(99)
ROOT = Path(__file__).resolve().parent.parent

CMP_SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt,
            ast.Gt: ast.GtE, ast.GtE: ast.Gt,
            ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add,
            ast.Mult: ast.Div, ast.Div: ast.Mult}


class Mutator(ast.NodeTransformer):
    """Visits mutation sites in a stable order; mutates site #target."""

    def __init__(self, target=-1):
        self.count = 0
        self.target = target
        self.applied = None      # (lineno, description) once mutated

    def _hit(self, lineno, desc):
        site = self.count
        self.count += 1
        if site == self.target:
            self.applied = (lineno, desc)
            return True
        return False

    def visit_Compare(self, node):
        self.generic_visit(node)
        for i, op in enumerate(node.ops):
            t = type(op)
            if t in CMP_SWAP and self._hit(
                    node.lineno, "%s -> %s" % (t.__name__,
                                               CMP_SWAP[t].__name__)):
                node.ops[i] = CMP_SWAP[t]()
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        t = type(node.op)
        swap = ast.Or if t is ast.And else ast.And
        if self._hit(node.lineno, "%s -> %s" % (t.__name__, swap.__name__)):
            node.op = swap()
        return node

    def visit_BinOp(self, node):
        self.generic_visit(node)
        t = type(node.op)
        if t in BIN_SWAP:
            # skip "+" on strings (formatting), keep numeric-looking ones —
            # cheap heuristic: skip when either side is a plain str constant
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    return node
            if self._hit(node.lineno, "%s -> %s" % (t.__name__,
                                                    BIN_SWAP[t].__name__)):
                node.op = BIN_SWAP[t]()
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            return node
        if self._hit(node.lineno, "%r -> %r" % (
                node.value,
                node.value + 1 if isinstance(node.value, int) else node.value * 2)):
            node.value = (node.value + 1 if isinstance(node.value, int)
                          else node.value * 2)
        return node


def count_sites(tree_src):
    m = Mutator(target=-1)
    m.visit(ast.parse(tree_src))
    return m.count


def make_mutant(tree_src, target):
    m = Mutator(target=target)
    tree = m.visit(ast.parse(tree_src))
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), m.applied


def run_suite(workdir):
    """True = suite green (mutant SURVIVED), False = killed."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
            cwd=str(workdir), capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False                 # hang = behavioral change = killed


def main():
    sample = 120
    if "--sample" in sys.argv[1:]:
        sample = int(sys.argv[sys.argv.index("--sample") + 1])
    src = (ROOT / "mind.py").read_text("utf-8")
    total = count_sites(src)
    targets = sorted(random.sample(range(total), min(sample, total)))
    print("mind mutation test — %d sites total, testing %d sampled mutants"
          % (total, len(targets)))
    print("=" * 64)

    killed, survivors, broken = 0, [], 0
    for n, target in enumerate(targets, 1):
        mutated, applied = make_mutant(src, target)
        if applied is None:
            continue
        tmp = Path(tempfile.mkdtemp(prefix="mind-mut-"))
        try:
            (tmp / "mind.py").write_text(mutated, encoding="utf-8")
            shutil.copytree(ROOT / "tests", tmp / "tests",
                            ignore=shutil.ignore_patterns("__pycache__"))
            try:
                compile(mutated, "mind.py", "exec")
            except SyntaxError:
                broken += 1
                continue
            if run_suite(tmp):
                survivors.append((applied[0], applied[1]))
            else:
                killed += 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if n % 20 == 0:
            print("  ... %d/%d done (killed %d, survived %d)"
                  % (n, len(targets), killed, len(survivors)))

    tested = killed + len(survivors)
    print("-" * 64)
    print("tested %d mutants: killed %d, survived %d  ->  kill rate %.0f%%"
          % (tested, killed, len(survivors),
             100.0 * killed / tested if tested else 0.0))
    for line, desc in survivors:
        print("  SURVIVOR mind.py:%d  %s" % (line, desc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
