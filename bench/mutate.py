#!/usr/bin/env python3
"""Deterministic mutation analysis with a self-validating test workspace.

The unmutated suite is run first in the exact staged layout. A red baseline
aborts classification. Every mutant receives a structured outcome and keeps
bounded diagnostics in the JSON report.
"""
import argparse
import ast
import concurrent.futures
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench.provenance import (  # noqa: E402
    repo_provenance, reproducible_command)

SEED = 99
DEFAULT_SAMPLE = 120
DEFAULT_TIMEOUT = 120
MAX_DIAGNOSTIC_CHARS = 50_000
RECHECK_OUTCOMES = frozenset({
    "killed",
    "timed_out",
    "infrastructure_error",
})
MUTATION_SOURCES = {
    "mind.py",
    "bench/longmemeval.py",
}

CMP_SWAP = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}
BIN_SWAP = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
}


class Mutator(ast.NodeTransformer):
    """Visit mutation sites in stable AST order and change one target."""

    def __init__(self, target=-1):
        self.count = 0
        self.target = target
        self.applied = None

    def _hit(self, lineno, description):
        site = self.count
        self.count += 1
        if site == self.target:
            self.applied = (lineno, description)
            return True
        return False

    def visit_Compare(self, node):
        self.generic_visit(node)
        for index, operator in enumerate(node.ops):
            operator_type = type(operator)
            if operator_type in CMP_SWAP and self._hit(
                    node.lineno, "%s -> %s" % (
                        operator_type.__name__,
                        CMP_SWAP[operator_type].__name__)):
                node.ops[index] = CMP_SWAP[operator_type]()
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        operator_type = type(node.op)
        swap = ast.Or if operator_type is ast.And else ast.And
        if self._hit(
                node.lineno, "%s -> %s" % (
                    operator_type.__name__, swap.__name__)):
            node.op = swap()
        return node

    def visit_BinOp(self, node):
        self.generic_visit(node)
        operator_type = type(node.op)
        if operator_type in BIN_SWAP:
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and isinstance(
                        side.value, str):
                    return node
            if self._hit(
                    node.lineno, "%s -> %s" % (
                        operator_type.__name__,
                        BIN_SWAP[operator_type].__name__)):
                node.op = BIN_SWAP[operator_type]()
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, bool) or not isinstance(
                node.value, (int, float)):
            return node
        replacement = (
            node.value + 1 if isinstance(node.value, int)
            else (1.0 if node.value == 0.0 else node.value * 2)
        )
        if self._hit(
                node.lineno, "%r -> %r" % (node.value, replacement)):
            node.value = replacement
        return node


def source_sha256(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def count_sites(source):
    mutator = Mutator(target=-1)
    mutator.visit(ast.parse(source))
    return mutator.count


def make_mutant(source, target):
    mutator = Mutator(target=target)
    tree = mutator.visit(ast.parse(source))
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), mutator.applied


def sampled_targets(total, sample, seed=SEED):
    rng = random.Random(seed)
    return sorted(rng.sample(range(total), min(sample, total)))


def prepare_workspace(destination):
    shutil.copy2(ROOT / "mind.py", destination / "mind.py")
    for directory in (
            "tests", "bench", "src", "tools", "contrib", "docs"):
        shutil.copytree(
            ROOT / directory,
            destination / directory,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "results", ".bench-cache"),
        )


def _failing_tests(output):
    return sorted(set(re.findall(
        r"^(?:FAIL|ERROR):\s+([^\s(]+(?:\s+\([^)]*\))?)",
        output,
        flags=re.MULTILINE,
    )))


def _diagnostic(text, workdir):
    text = text or ""
    replacements = {
        str(Path(workdir)): "<workspace>",
        str(Path(workdir).resolve()): "<workspace>",
        str(Path(tempfile.gettempdir())): "<tmp>",
        str(Path(tempfile.gettempdir()).resolve()): "<tmp>",
        str(Path(sys.executable)): "<python>",
        str(Path(sys.executable).resolve()): "<python>",
    }
    for runtime_root in {
            sys.prefix,
            sys.exec_prefix,
            getattr(sys, "base_prefix", sys.prefix),
            getattr(sys, "base_exec_prefix", sys.exec_prefix),
    }:
        replacements[str(Path(runtime_root))] = "<python-root>"
        replacements[str(Path(runtime_root).resolve())] = "<python-root>"
    for value, replacement in sorted(
            replacements.items(), key=lambda item: -len(item[0])):
        text = text.replace(value, replacement)
    return text[-MAX_DIAGNOSTIC_CHARS:]


def run_suite(workdir, timeout=DEFAULT_TIMEOUT):
    started = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover",
             "-s", "tests", "-q"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "")
        stderr = (exc.stderr or "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return {
            "outcome": "timed_out",
            "returncode": None,
            "duration_ms": round(
                (time.perf_counter() - started) * 1000, 3),
            "stdout": _diagnostic(stdout, workdir),
            "stderr": _diagnostic(stderr, workdir),
            "failing_tests": [],
        }
    combined = result.stdout + "\n" + result.stderr
    if result.returncode == 0:
        outcome = "survived"
    elif re.search(r"Ran\s+\d+\s+tests?", combined):
        outcome = "killed"
    else:
        outcome = "infrastructure_error"
    return {
        "outcome": outcome,
        "returncode": result.returncode,
        "duration_ms": round(
            (time.perf_counter() - started) * 1000, 3),
        "stdout": _diagnostic(result.stdout, workdir),
        "stderr": _diagnostic(result.stderr, workdir),
        "failing_tests": _failing_tests(combined),
    }


def _sync_modular_mutant(workdir, mutated_source):
    source_dir = workdir / "src" / "mind"
    manifest_path = source_dir / "source.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    names = manifest.get("fragments")
    markers = (
        "\ndef _bigrams",
        "\nclass CommandEmbed",
        "\nclass Hippocampus",
        "\nclass Cortex",
        "\nclass Dreamer",
        "\ndef _invocation",
        "\nclass PolicyEngine",
        "\nclass LifecycleManager",
        "\nclass Mind",
    )
    if not isinstance(names, list) or len(names) != len(markers) + 1:
        raise ValueError("unexpected modular source manifest")
    positions = [0]
    for marker in markers:
        position = mutated_source.find(marker)
        if position < 0:
            raise ValueError(
                "mutated source boundary not found: %s" % marker.strip())
        positions.append(position + 1)
    positions.append(len(mutated_source))
    for index, name in enumerate(names):
        fragment = mutated_source[
            positions[index]:positions[index + 1]]
        compile(fragment, str(source_dir / name), "exec")
        (source_dir / name).write_text(
            fragment, encoding="utf-8")
    (source_dir / "source.json").write_text(
        json.dumps({
            "format": 1,
            "artifact": "mind.py",
            "fragments": names,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def classify_mutant(
        workdir, source_relative, mutated_source, timeout):
    try:
        compile(mutated_source, "mind.py", "exec")
    except SyntaxError as exc:
        return {
            "outcome": "compile_error",
            "returncode": None,
            "duration_ms": 0.0,
            "stdout": "",
            "stderr": "%s" % exc,
            "failing_tests": [],
        }
    target = workdir / source_relative
    target.write_text(mutated_source, encoding="utf-8")
    if source_relative == "mind.py":
        _sync_modular_mutant(workdir, mutated_source)
    return run_suite(workdir, timeout=timeout)


def confirm_parallel_candidates(
        initial_results, selected, execute, workers):
    """Re-run every non-surviving parallel result without contention."""
    final_results = []
    rechecked = 0
    reclassified = 0
    for record, initial in zip(selected, initial_results):
        if workers == 1:
            initial["execution_mode"] = "isolated"
            final_results.append(initial)
            continue
        initial["execution_mode"] = "parallel"
        if initial["outcome"] not in RECHECK_OUTCOMES:
            final_results.append(initial)
            continue
        rechecked += 1
        confirmation = execute(record)
        confirmation["execution_mode"] = "isolated_confirmation"
        confirmation["initial_attempt"] = initial
        changed = confirmation["outcome"] != initial["outcome"]
        confirmation["reclassified_parallel_noise"] = changed
        reclassified += int(changed)
        final_results.append(confirmation)
    return final_results, {
        "candidate_rechecks": rechecked,
        "parallel_noise_reclassified": reclassified,
    }


def default_report_path(source_relative, source_digest):
    stem = source_relative.replace("/", "-").replace(".", "-")
    return ROOT / "bench" / "results" / (
        "mutation-%s-%s.json" % (stem, source_digest[:12]))


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample", type=int, default=DEFAULT_SAMPLE,
        help="Number of deterministic mutation sites to test.",
    )
    parser.add_argument(
        "--source", choices=sorted(MUTATION_SOURCES),
        default="mind.py",
        help="Repository-relative source to mutate.",
    )
    parser.add_argument(
        "--sequences",
        help=(
            "Comma-separated one-based ordinals from the deterministic "
            "sample, for focused regression reruns."),
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT,
        help="Per-suite timeout in seconds.",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Independent mutant test processes to run concurrently.",
    )
    parser.add_argument(
        "--json-out",
        help="Structured report path. Defaults under bench/results/.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.sample <= 0:
        raise SystemExit("--sample must be a positive integer")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    if not (1 <= args.workers <= 16):
        raise SystemExit("--workers must be between 1 and 16")

    source_path = ROOT / args.source
    source = source_path.read_text("utf-8")
    digest = source_sha256(source)
    total = count_sites(source)
    sampled = sampled_targets(total, args.sample)
    if args.sequences:
        try:
            sequences = [
                int(value) for value in args.sequences.split(",")
                if value.strip()
            ]
        except ValueError:
            raise SystemExit("--sequences must contain positive integers")
        if not sequences or any(
                sequence <= 0 or sequence > len(sampled)
                for sequence in sequences):
            raise SystemExit(
                "--sequences values must be within the deterministic sample")
        selected = [
            (sequence, sampled[sequence - 1])
            for sequence in sequences
        ]
    else:
        selected = list(enumerate(sampled, 1))
    targets = [target for _, target in selected]
    report_path = Path(args.json_out) if args.json_out else \
        default_report_path(args.source, digest)
    report = {
        "format": 2,
        "benchmark": "deterministic-mutation-v3",
        "command": reproducible_command(),
        "provenance": repo_provenance((
            args.source,
            "bench/mutate.py",
        )),
        "seed": SEED,
        "corpus": {
            "id": "mind-ast-operators-v2",
            "generator_sha256": source_sha256(
                (ROOT / "bench" / "mutate.py").read_text("utf-8")),
            "requested_sample": args.sample,
            "selected_sequences": [
                sequence for sequence, _ in selected
            ],
        },
        "source": args.source,
        "source_sha256": digest,
        "site_count": total,
        "sample_size": len(targets),
        "targets": targets,
        "baseline": None,
        "mutants": [],
        "summary": {},
    }

    print("mind mutation test - %s - %d sites, %d sampled mutants" % (
        args.source, total, len(targets)))
    print("=" * 64)
    baseline_dir = Path(tempfile.mkdtemp(prefix="mind-mut-baseline-"))
    try:
        prepare_workspace(baseline_dir)
        baseline = run_suite(baseline_dir, timeout=args.timeout)
    finally:
        shutil.rmtree(baseline_dir, ignore_errors=True)
    report["baseline"] = baseline
    if baseline["outcome"] != "survived":
        report["summary"] = {"infrastructure_error": 1}
        write_report(report_path, report)
        print("baseline: RED (%s); no mutants classified" %
              baseline["outcome"])
        print("report: %s" % report_path)
        return 1
    print("baseline: GREEN")

    counts = {
        "killed": 0,
        "survived": 0,
        "timed_out": 0,
        "compile_error": 0,
        "invalid": 0,
        "infrastructure_error": 0,
    }
    def execute(record):
        sequence, target = record
        mutated, applied = make_mutant(source, target)
        if applied is None:
            return {
                "outcome": "invalid",
                "returncode": None,
                "duration_ms": 0.0,
                "stdout": "",
                "stderr": "deterministic target was not applied",
                "failing_tests": [],
                "sequence": sequence,
                "target": target,
                "line": None,
                "mutation": None,
            }
        workspace = Path(tempfile.mkdtemp(prefix="mind-mut-"))
        try:
            prepare_workspace(workspace)
            result = classify_mutant(
                workspace, args.source, mutated, args.timeout)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
        result.update({
            "sequence": sequence,
            "target": target,
            "line": applied[0],
            "mutation": applied[1],
        })
        return result

    if args.workers == 1:
        initial_results = list(map(execute, selected))
    else:
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=args.workers)
        try:
            initial_results = list(executor.map(execute, selected))
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
    for progress in range(20, len(targets) + 1, 20):
        print("  ... %d/%d initial runs done" % (
            progress, len(targets)))
    final_results, confirmation = confirm_parallel_candidates(
        initial_results, selected, execute, args.workers)
    if confirmation["candidate_rechecks"]:
        print(
            "  ... %d non-surviving candidates rechecked in isolation; "
            "%d parallel outcomes reclassified" % (
                confirmation["candidate_rechecks"],
                confirmation["parallel_noise_reclassified"],
            ))
    for result in final_results:
        counts[result["outcome"]] += 1
        report["mutants"].append(result)

    attempted = sum(counts.values())
    classified = counts["killed"] + counts["survived"]
    kill_rate = (
        counts["killed"] / classified if classified else 0.0)
    report["summary"] = dict(counts)
    report["summary"]["attempted"] = attempted
    report["summary"]["classified"] = classified
    report["summary"]["kill_rate"] = kill_rate
    report["summary"].update(confirmation)
    manifest_rows = [
        {
            "sequence": mutant["sequence"],
            "target": mutant["target"],
            "line": mutant["line"],
            "mutation": mutant["mutation"],
        }
        for mutant in report["mutants"]
    ]
    report["corpus"]["manifest_sha256"] = source_sha256(
        json.dumps(
            manifest_rows,
            sort_keys=True,
            separators=(",", ":"),
        ))
    write_report(report_path, report)

    print("-" * 64)
    print(
        "attempted %d; behaviorally classified %d: killed %d, "
        "survived %d, timed out %d, "
        "compile errors %d, infrastructure errors %d" % (
            attempted, classified, counts["killed"], counts["survived"],
            counts["timed_out"], counts["compile_error"],
            counts["infrastructure_error"]))
    print("kill rate: %.1f%%" % (kill_rate * 100.0))
    for mutant in report["mutants"]:
        if mutant["outcome"] == "survived":
            print("  SURVIVOR %s:%d %s" % (
                args.source, mutant["line"], mutant["mutation"]))
    print("report: %s" % report_path)
    return 0 if counts["infrastructure_error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
