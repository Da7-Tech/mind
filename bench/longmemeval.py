#!/usr/bin/env python3
"""LongMemEval retrieval benchmark harness for mind.

This maps LongMemEval history sessions to Hippocampus.remember() calls, then
maps each benchmark question to Hippocampus.recall(). It measures retrieval:
whether recall returns the evidence session/turn, not whether a language model
can synthesize the final answer.

Run:
  python3 bench/longmemeval.py --limit 50

By default the script downloads the official cleaned LongMemEval oracle file
from Hugging Face into .bench-cache/. Pass --data to use an existing JSON file.
"""
import argparse
import hashlib
import json
import os
import random
import shutil
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mind import Hippocampus  # noqa: E402

DEFAULT_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
    "resolve/main/longmemeval_oracle.json"
)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_commit():
    root = Path(__file__).resolve().parent.parent
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def is_url(value):
    return value.startswith("http://") or value.startswith("https://")


def download(url, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / url.rsplit("/", 1)[-1].split("?", 1)[0]
    if target.exists():
        return target
    tmp = target.with_suffix(target.suffix + ".tmp")
    req = urllib.request.Request(url, headers={"User-Agent": "mind-longmemeval-bench"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            "could not download %s (%s); download it manually and pass --data PATH" % (url, e)
        )
    os.replace(tmp, target)
    return target


def resolve_data(data, cache_dir):
    source = data or DEFAULT_URL
    if is_url(source):
        return download(source, cache_dir), source
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(source)
    return path, str(path)


def load_instances(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("LongMemEval data must be a JSON list")
    return data


def norm(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(norm(v) for v in value)
    return " ".join(str(value).lower().split())


def answer_in_text(answer, text):
    answer = norm(answer)
    if not answer:
        return False
    return answer in norm(text)


def turn_text(question_id, session_id, date, role, content):
    return "[%s] question %s session %s %s: %s" % (
        date or "unknown-date",
        question_id,
        session_id,
        role or "unknown-role",
        content,
    )


def session_text(question_id, session_id, date, turns):
    parts = []
    for turn in turns:
        parts.append("%s: %s" % (turn.get("role", "unknown-role"), turn.get("content", "")))
    return "[%s] question %s session %s\n%s" % (
        date or "unknown-date",
        question_id,
        session_id,
        "\n".join(parts),
    )


def remember_instance(instance, h, granularity):
    qid = str(instance.get("question_id", "unknown"))
    answer_sessions = set(str(s) for s in instance.get("answer_session_ids", []) or [])
    session_ids = [str(s) for s in instance.get("haystack_session_ids", []) or []]
    dates = instance.get("haystack_dates", []) or []
    sessions = instance.get("haystack_sessions", []) or []
    evidence_nodes = set()
    total = 0

    for i, turns in enumerate(sessions):
        session_id = session_ids[i] if i < len(session_ids) else str(i)
        date = dates[i] if i < len(dates) else ""
        session_is_evidence = session_id in answer_sessions
        if granularity == "session":
            text = session_text(qid, session_id, date, turns)
            nid = h.remember(text)
            total += 1
            if session_is_evidence or any(t.get("has_answer") for t in turns):
                evidence_nodes.add(nid)
            continue
        for turn in turns:
            text = turn_text(qid, session_id, date, turn.get("role"), turn.get("content", ""))
            nid = h.remember(text)
            total += 1
            if session_is_evidence or turn.get("has_answer"):
                evidence_nodes.add(nid)

    return evidence_nodes, total


def should_skip_abstention(instance, include_abstention):
    if include_abstention:
        return False
    qid = str(instance.get("question_id", ""))
    return qid.endswith("_abs")


def select_subset(instances, limit, seed, include_abstention):
    pool = [i for i in instances if not should_skip_abstention(i, include_abstention)]
    rng = random.Random(seed)
    rng.shuffle(pool)
    return pool[:limit] if limit else pool


def p95(values):
    if not values:
        return 0.0
    return sorted(values)[max(0, int(len(values) * 0.95) - 1)]


def evaluate(instances, limit=50, seed=13, top_k=5, granularity="turn",
             include_abstention=False):
    selected = select_subset(instances, limit, seed, include_abstention)
    tmp = Path(tempfile.mkdtemp(prefix="mind-longmemeval-"))
    metrics = {
        "evaluated": 0,
        "skipped_no_evidence": 0,
        "skipped_abstention": len(instances) - len(
            [i for i in instances if not should_skip_abstention(i, include_abstention)]
        ),
        "memory_records": 0,
        "evidence_at_1": 0,
        "evidence_at_k": 0,
        "answer_string_at_k": 0,
        "latencies_ms": [],
    }
    try:
        for idx, instance in enumerate(selected):
            h = Hippocampus(tmp / ("%04d.json" % idx))
            evidence_nodes, records = remember_instance(instance, h, granularity)
            metrics["memory_records"] += records
            if not evidence_nodes:
                metrics["skipped_no_evidence"] += 1
                continue

            t0 = time.perf_counter()
            results, _, _ = h.recall(instance.get("question", ""), top_k=top_k)
            metrics["latencies_ms"].append((time.perf_counter() - t0) * 1000)
            top_nodes = [nid for nid, _, _ in results]
            top_texts = [node["text"] for _, _, node in results]
            metrics["evaluated"] += 1
            if top_nodes[:1] and top_nodes[0] in evidence_nodes:
                metrics["evidence_at_1"] += 1
            if any(nid in evidence_nodes for nid in top_nodes[:top_k]):
                metrics["evidence_at_k"] += 1
            if any(answer_in_text(instance.get("answer"), text) for text in top_texts[:top_k]):
                metrics["answer_string_at_k"] += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n = max(1, metrics["evaluated"])
    metrics["evidence_at_1_rate"] = metrics["evidence_at_1"] / n
    metrics["evidence_at_k_rate"] = metrics["evidence_at_k"] / n
    metrics["answer_string_at_k_rate"] = metrics["answer_string_at_k"] / n
    metrics["median_latency_ms"] = (
        statistics.median(metrics["latencies_ms"]) if metrics["latencies_ms"] else 0.0
    )
    metrics["p95_latency_ms"] = p95(metrics["latencies_ms"])
    metrics["avg_memory_records"] = (
        metrics["memory_records"] / metrics["evaluated"] if metrics["evaluated"] else 0.0
    )
    return metrics


def print_report(metrics, source, digest, args):
    command = shlex.join([Path(sys.executable).name] + sys.argv)
    print("LongMemEval retrieval benchmark (mind)")
    print("=" * 56)
    print("source: %s" % source)
    print("dataset sha256: %s" % digest)
    print("commit: %s" % repo_commit())
    print("command: %s" % command)
    print("subset: limit=%s seed=%s granularity=%s top_k=%s" % (
        args.limit or "all", args.seed, args.granularity, args.top_k))
    print("evaluated: %d | skipped abstention: %d | skipped no-evidence: %d" % (
        metrics["evaluated"], metrics["skipped_abstention"], metrics["skipped_no_evidence"]))
    print("memory records: %d total | %.1f avg/question" % (
        metrics["memory_records"], metrics["avg_memory_records"]))
    print("evidence@1 %.3f | evidence@%d %.3f | answer-string@%d %.3f" % (
        metrics["evidence_at_1_rate"], args.top_k, metrics["evidence_at_k_rate"],
        args.top_k, metrics["answer_string_at_k_rate"]))
    print("latency median %.2f ms | p95 %.2f ms" % (
        metrics["median_latency_ms"], metrics["p95_latency_ms"]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", help="Local JSON file or URL. Defaults to official cleaned oracle JSON.")
    p.add_argument("--cache-dir", default=".bench-cache", help="Download cache directory.")
    p.add_argument("--limit", type=int, default=50, help="Number of non-abstention questions to sample.")
    p.add_argument("--seed", type=int, default=13, help="Deterministic subset seed.")
    p.add_argument("--top-k", type=int, default=5, help="Recall cutoff for evidence@k.")
    p.add_argument("--granularity", choices=("turn", "session"), default="turn")
    p.add_argument("--include-abstention", action="store_true",
                   help="Include *_abs questions even though official retrieval metrics skip them.")
    p.add_argument("--json-out", help="Optional path for a JSON metrics report.")
    args = p.parse_args(argv)

    path, source = resolve_data(args.data, Path(args.cache_dir))
    instances = load_instances(path)
    metrics = evaluate(
        instances,
        limit=args.limit,
        seed=args.seed,
        top_k=args.top_k,
        granularity=args.granularity,
        include_abstention=args.include_abstention,
    )
    digest = sha256_file(path)
    print_report(metrics, source, digest, args)
    if args.json_out:
        report = dict(metrics)
        report["source"] = source
        report["dataset_sha256"] = digest
        report["commit"] = repo_commit()
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=True)
            f.write("\n")
    return 0 if metrics["evaluated"] else 1


if __name__ == "__main__":
    sys.exit(main())
