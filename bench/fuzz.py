#!/usr/bin/env python3
"""mind fuzzer — deterministic, seeded, zero dependencies.

Two hostile surfaces, two contracts:

1. GRAPH FUZZ — feed `Hippocampus` adversarial graph.json contents
   (malformed JSON, wrong-typed fields, NaN/Infinity, deep nesting,
   control characters, huge values, dangling edges). Contract: load
   never raises, every core operation (recall / remember / decay /
   dream / export) completes, and the graph saved afterwards is valid
   JSON that loads clean.

2. CLI FUZZ — drive the real argv dispatcher with hostile inputs
   (random unicode, RTL, emoji, ANSI escapes, flag-like text, very
   long args). Contract: the process never prints a Python traceback
   and never exits with a code outside {0, 1, 2}.

Deterministic (seeded): rerun it and you get the same cases. Runs in CI.

Run:  python3 bench/fuzz.py            (full: 300 graph + 120 cli cases)
      python3 bench/fuzz.py --quick    (CI: 120 graph + 40 cli cases)
"""
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mind as M                                    # noqa: E402

random.seed(1337)

SCALARS = [None, True, False, 0, -1, 1e308, -1e308, float("nan"),
           float("inf"), float("-inf"), "", "x", "0", "\x1b[31mred\x1b[0m",
           "‮RTL‬", "😱" * 5, "a" * 10000, [], {}, [1, 2], {"k": "v"},
           12345, -0.5, 3.5]
KEYS = ["text", "weight", "peak_weight", "confidence", "access_count",
        "keys", "last_accessed", "created", "history"]
TEXT_POOL = ["deploy target is hetzner", "قاعدة البيانات بوستغرس",
             "rate limit is 100", "", "x", "😱 emoji fact",
             "\x1b[2Jcleared", "a" * 500, "--dry-run", "-h", "nested {json}",
             '"; rm -rf / #', "line\nbreak\ttab"]


def rand_node():
    node = {}
    for k in random.sample(KEYS, random.randint(0, len(KEYS))):
        node[k] = random.choice(SCALARS)
    if random.random() < 0.6:
        node["text"] = random.choice(TEXT_POOL)
    return random.choice([node, random.choice(SCALARS)])


def rand_graph():
    """A graph document that is hostile but often plausible-looking."""
    r = random.random()
    if r < 0.15:                                   # not even JSON
        return random.choice(["", "{", "not json at all", "[1,2,3",
                              "\x00\x01\x02", '{"nodes": '])
    if r < 0.25:                                   # JSON, wrong shape
        return json.dumps(random.choice([[], 42, "str", None, {"nodes": 7},
                                         {"edges": [1, 2]}]))
    nodes = {("n%d" % i): rand_node() for i in range(random.randint(0, 6))}
    edges = {}
    for _ in range(random.randint(0, 6)):
        a = random.choice(list(nodes) + ["ghost", "", "n0"])
        edges[a] = random.choice([
            random.choice(SCALARS),
            {random.choice(list(nodes) + ["ghost"]):
             random.choice([{"relation": "r", "weight": random.choice(SCALARS)},
                            random.choice(SCALARS)])},
        ])
    # json.dumps with NaN/Infinity produces non-standard JSON on purpose —
    # exactly the kind of file another (buggier) tool could leave behind
    return json.dumps({"nodes": nodes, "edges": edges})


def graph_case(doc):
    """One graph-fuzz case. Returns None on success, error string on failure."""
    tmp = Path(tempfile.mkdtemp(prefix="mind-fuzz-"))
    try:
        mind_dir = tmp / ".mind"
        (mind_dir / "cortex").mkdir(parents=True)
        (mind_dir / "dreams").mkdir()
        gpath = mind_dir / "graph.json"
        gpath.write_text(doc, encoding="utf-8")
        h = M.Hippocampus(gpath)
        h.recall("which database do we use")
        h.recall("قاعدة البيانات")
        h.remember("fuzz fact %d" % random.randint(0, 9))
        h.decay()
        c = M.Cortex(mind_dir / "cortex")
        M.Dreamer(mind_dir, h, c).dream()
        M.Active(mind_dir, h, c).generate(tmp)
        # the graph left behind must load clean in a fresh instance
        reloaded = M.Hippocampus(gpath)
        json.loads(gpath.read_text("utf-8"))
        assert any("fuzz fact" in n.get("text", "")
                   for n in reloaded.nodes.values()), "write was lost"
        return None
    except Exception as e:                          # noqa: BLE001 — the contract
        return "%s: %r (doc=%.120r)" % (type(e).__name__, e, doc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cli_case(args):
    """One CLI-fuzz case. Contract: no traceback, exit code in {0,1,2}."""
    here = Path(__file__).resolve().parent.parent / "mind.py"
    proj = Path(tempfile.mkdtemp(prefix="mind-fuzz-cli-"))
    try:
        subprocess.run([sys.executable, str(here), "init"], cwd=str(proj),
                       capture_output=True, text=True, timeout=30)
        r = subprocess.run([sys.executable, str(here), *args], cwd=str(proj),
                           capture_output=True, text=True, timeout=30)
        if "Traceback (most recent call last)" in r.stderr:
            return "traceback for argv=%r:\n%s" % (args, r.stderr[-500:])
        if r.returncode not in (0, 1, 2):
            return "exit %d for argv=%r" % (r.returncode, args)
        return None
    except subprocess.TimeoutExpired:
        return "timeout for argv=%r" % (args,)
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def rand_argv():
    cmds = ["init", "remember", "link", "recall", "confirm", "correct",
            "dream", "export", "status", "bogus", "--dry-run", ""]
    argv = [random.choice(cmds)]
    for _ in range(random.randint(0, 3)):
        argv.append(random.choice(TEXT_POOL))
    return argv


def main():
    quick = "--quick" in sys.argv[1:]
    n_graph, n_cli = (120, 40) if quick else (300, 120)
    failures = []

    for i in range(n_graph):
        err = graph_case(rand_graph())
        if err:
            failures.append("graph[%d] %s" % (i, err))
    print("graph fuzz: %d cases, %d failures" % (n_graph, len(failures)))

    cli_fail_before = len(failures)
    for i in range(n_cli):
        err = cli_case(rand_argv())
        if err:
            failures.append("cli[%d] %s" % (i, err))
    print("cli fuzz:   %d cases, %d failures"
          % (n_cli, len(failures) - cli_fail_before))

    for f in failures[:10]:
        print("  FAIL %s" % f)
    if len(failures) > 10:
        print("  ... and %d more" % (len(failures) - 10))
    print("verdict: %s" % ("PASS" if not failures else "FAIL"))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
