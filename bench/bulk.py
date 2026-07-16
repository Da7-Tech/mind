#!/usr/bin/env python3
"""Measure transactional bulk ingest and a conservative serial lower bound."""
import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mind import GRAPH_FILE, Hippocampus  # noqa: E402
from bench.provenance import (  # noqa: E402
    repo_provenance, reproducible_command)


def facts(count):
    return [
        "bulk benchmark durable fact %05d uses component %03d"
        % (index, index % 251)
        for index in range(count)
    ]


def instrument(hippo):
    counts = {"commits": 0, "journal_batches": 0, "signal_batches": 0}
    original_commit = hippo._commit_current
    original_journal = hippo._journal_batch_immediate
    original_signals = hippo._log_signals_immediate

    def commit():
        counts["commits"] += 1
        return original_commit()

    def journal(records):
        counts["journal_batches"] += 1
        return original_journal(records)

    def signals(records):
        counts["signal_batches"] += 1
        return original_signals(records)

    hippo._commit_current = commit
    hippo._journal_batch_immediate = journal
    hippo._log_signals_immediate = signals
    return counts


def measure_batch(records):
    root = Path(tempfile.mkdtemp(prefix="mind-bulk-batch-"))
    try:
        hippo = Hippocampus(root / GRAPH_FILE)
        counts = instrument(hippo)
        started = time.perf_counter()
        node_ids = hippo.remember_many(records)
        elapsed = time.perf_counter() - started
        reopened = Hippocampus(root / GRAPH_FILE)
        return elapsed, counts, len(node_ids), len(reopened.nodes)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def measure_serial(records, repetitions):
    samples = []
    aggregate = {"commits": 0, "journal_batches": 0, "signal_batches": 0}
    for repetition in range(repetitions):
        root = Path(tempfile.mkdtemp(prefix="mind-bulk-serial-"))
        try:
            hippo = Hippocampus(root / GRAPH_FILE)
            counts = instrument(hippo)
            started = time.perf_counter()
            for text in records:
                hippo.remember(text)
            samples.append(time.perf_counter() - started)
            for key in aggregate:
                aggregate[key] += counts[key]
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return statistics.median(samples), aggregate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--serial-sample", type=int, default=200)
    parser.add_argument("--serial-repetitions", type=int, default=3)
    parser.add_argument("--json-out")
    args = parser.parse_args(argv)
    if not (1 <= args.records <= 10_000):
        parser.error("--records must be between 1 and 10000")
    if not (1 <= args.serial_sample <= args.records):
        parser.error("--serial-sample must be positive and <= --records")
    if args.serial_repetitions <= 0:
        parser.error("--serial-repetitions must be positive")

    corpus = facts(args.records)
    batch_seconds, batch_counts, returned, reopened = measure_batch(corpus)
    serial_seconds, serial_counts = measure_serial(
        corpus[:args.serial_sample], args.serial_repetitions)
    conservative_serial_seconds = (
        serial_seconds / args.serial_sample * args.records)
    speedup = conservative_serial_seconds / batch_seconds
    report = {
        "benchmark": "transactional-bulk-ingest-v1",
        "command": reproducible_command(),
        "provenance": repo_provenance(("bench/bulk.py",)),
        "records": args.records,
        "serial_sample": args.serial_sample,
        "serial_repetitions": args.serial_repetitions,
        "batch_seconds": batch_seconds,
        "serial_sample_median_seconds": serial_seconds,
        "conservative_serial_seconds": conservative_serial_seconds,
        "conservative_lower_bound_speedup": speedup,
        "batch_counts": batch_counts,
        "serial_counts": serial_counts,
        "returned_ids": returned,
        "reopened_nodes": reopened,
        "method": (
            "serial projection uses the measured average cost of the first "
            "sample as a lower bound; later serial writes process a no-smaller "
            "graph, so the projection does not overstate speedup"),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0 if (
        returned == args.records
        and reopened == args.records
        and batch_counts["commits"] == 1
        and batch_counts["journal_batches"] == 1
        and batch_counts["signal_batches"] == 1
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
