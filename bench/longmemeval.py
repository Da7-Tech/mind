#!/usr/bin/env python3
"""Reproducible LongMemEval retrieval benchmark harness for mind.

The default dataset is pinned by revision and SHA-256 in
bench/manifests/longmemeval.json. Embedding configuration is scrubbed from
the ambient environment unless explicitly supplied on this command line.
"""
import argparse
import contextlib
import hashlib
import json
import math
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = (
    ROOT / "bench" / "manifests" / "longmemeval.json")

sys.path.insert(0, str(ROOT))
from mind import Hippocampus, _tokenize  # noqa: E402
from bench.provenance import (  # noqa: E402
    repo_provenance, reproducible_command, sha256_file)


def positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be a positive integer")
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def load_manifest(path=DEFAULT_MANIFEST):
    data = json.loads(Path(path).read_text("utf-8"))
    required = ("url", "sha256", "revision", "subset")
    if not isinstance(data, dict) or any(
            field not in data for field in required):
        raise ValueError("invalid LongMemEval manifest")
    if not isinstance(data["subset"], dict):
        raise ValueError("invalid LongMemEval subset manifest")
    if not isinstance(data["sha256"], str) or len(data["sha256"]) != 64:
        raise ValueError("invalid LongMemEval manifest digest")
    return data


def is_url(value):
    return value.startswith("http://") or value.startswith("https://")


def _cache_target(url, cache_dir, expected_sha256=None):
    basename = url.rsplit("/", 1)[-1].split("?", 1)[0]
    source_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    digest_key = (expected_sha256 or "unverified")[:16]
    return cache_dir / ("%s-%s-%s" % (
        source_key, digest_key, basename))


def verify_digest(path, expected_sha256):
    actual = sha256_file(path)
    if expected_sha256 and actual != expected_sha256:
        raise ValueError(
            "dataset digest mismatch: expected %s, got %s" % (
                expected_sha256, actual))
    return actual


def download(url, cache_dir, expected_sha256=None):
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = _cache_target(url, cache_dir, expected_sha256)
    if target.exists():
        try:
            verify_digest(target, expected_sha256)
            return target
        except ValueError:
            target.unlink()
    tmp = target.with_suffix(target.suffix + ".tmp")
    request = urllib.request.Request(
        url, headers={"User-Agent": "mind-longmemeval-bench"})
    try:
        with urllib.request.urlopen(
                request, timeout=60) as response, open(tmp, "wb") as handle:
            shutil.copyfileobj(response, handle)
        verify_digest(tmp, expected_sha256)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            "could not download and verify %s (%s); pass --data PATH" % (
                url, exc))
    os.replace(tmp, target)
    return target


def resolve_data(data, cache_dir, expected_sha256=None):
    source = data
    if is_url(source):
        path = download(source, cache_dir, expected_sha256)
    else:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(source)
        verify_digest(path, expected_sha256)
    return path, str(source)


def load_instances(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("LongMemEval data must be a JSON list")
    return data


def norm(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(norm(item) for item in value)
    return " ".join(str(value).lower().split())


def answer_in_text(answer, text):
    answer = norm(answer)
    return bool(answer) and answer in norm(text)


def turn_text(question_id, session_id, date, role, content):
    return "[%s] question %s session %s %s: %s" % (
        date or "unknown-date",
        question_id,
        session_id,
        role or "unknown-role",
        content,
    )


def session_text(question_id, session_id, date, turns):
    parts = [
        "%s: %s" % (
            turn.get("role", "unknown-role"),
            turn.get("content", ""))
        for turn in turns
    ]
    return "[%s] question %s session %s\n%s" % (
        date or "unknown-date",
        question_id,
        session_id,
        "\n".join(parts),
    )


def remember_instance(instance, hippo, granularity):
    question_id = str(instance.get("question_id", "unknown"))
    answer_sessions = set(
        str(value) for value in (
            instance.get("answer_session_ids", []) or []))
    session_ids = [
        str(value) for value in (
            instance.get("haystack_session_ids", []) or [])
    ]
    dates = instance.get("haystack_dates", []) or []
    sessions = instance.get("haystack_sessions", []) or []
    evidence_nodes = set()
    records = []
    for index, turns in enumerate(sessions):
        session_id = (
            session_ids[index] if index < len(session_ids)
            else str(index))
        date = dates[index] if index < len(dates) else ""
        session_is_evidence = session_id in answer_sessions
        session_has_marked_turn = any(
            turn.get("has_answer") for turn in turns)
        if granularity == "session":
            text = session_text(
                question_id, session_id, date, turns)
            records.append(text)
            if session_is_evidence or session_has_marked_turn:
                evidence_nodes.add(hippo._id(text))
            continue
        for turn in turns:
            text = turn_text(
                question_id, session_id, date,
                turn.get("role"), turn.get("content", ""))
            records.append(text)
            if turn.get("has_answer") or (
                    session_is_evidence and
                    not session_has_marked_turn):
                evidence_nodes.add(hippo._id(text))
    hippo.remember_many(records)
    return evidence_nodes, len(records)


def should_skip_abstention(instance, include_abstention):
    return (
        not include_abstention
        and str(instance.get("question_id", "")).endswith("_abs")
    )


def select_subset(instances, limit, seed, include_abstention):
    pool = [
        instance for instance in instances
        if not should_skip_abstention(instance, include_abstention)
    ]
    random.Random(seed).shuffle(pool)
    return pool[:limit] if limit else pool


def p95(values):
    if not values:
        return 0.0
    return sorted(values)[max(0, int(len(values) * 0.95) - 1)]


class BM25Baseline:
    """Dependency-free lexical baseline using the identical record mapping."""

    def __init__(self):
        self.records = {}

    _id = staticmethod(Hippocampus._id)

    def remember_many(self, records):
        node_ids = []
        for text in records:
            node_id = self._id(text)
            self.records[node_id] = text
            node_ids.append(node_id)
        return node_ids

    @staticmethod
    def _terms(text):
        return [term.lower() for term in _tokenize(text)]

    def recall(self, query, top_k=5):
        query_terms = self._terms(query)
        documents = [
            (node_id, text, self._terms(text))
            for node_id, text in self.records.items()
        ]
        if not query_terms or not documents:
            return [], {}, {}
        document_count = len(documents)
        average_length = (
            sum(len(terms) for _, _, terms in documents)
            / document_count
        ) or 1.0
        document_frequency = {
            term: sum(
                term in terms for _, _, terms in documents)
            for term in set(query_terms)
        }
        k1, b = 1.5, 0.75
        ranked = []
        for node_id, text, terms in documents:
            score = 0.0
            length = len(terms)
            for term in query_terms:
                frequency = terms.count(term)
                if not frequency:
                    continue
                frequency_in_documents = document_frequency[term]
                inverse_document_frequency = math.log(
                    1.0 + (
                        document_count - frequency_in_documents + 0.5
                    ) / (frequency_in_documents + 0.5)
                )
                denominator = frequency + k1 * (
                    1.0 - b + b * length / average_length)
                score += inverse_document_frequency * (
                    frequency * (k1 + 1.0) / denominator)
            if score:
                ranked.append(
                    (node_id, score, {"text": text}))
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return ranked[:top_k], {}, {}


@contextlib.contextmanager
def embedding_environment(
        embed_cmd=None, embed_server=None, embed_timeout=None):
    names = (
        "MIND_EMBED_CMD", "MIND_EMBED_SERVER",
        "MIND_EMBED_TIMEOUT", "MIND_EMBED_BUDGET")
    previous = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ.pop(name, None)
    if embed_cmd:
        os.environ["MIND_EMBED_CMD"] = embed_cmd
    if embed_server:
        os.environ["MIND_EMBED_SERVER"] = embed_server
    if embed_timeout is not None:
        os.environ["MIND_EMBED_TIMEOUT"] = str(embed_timeout)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def evaluate(instances, limit=50, seed=13, top_k=5,
             granularity="turn", include_abstention=False,
             embed_cmd=None, embed_server=None, embed_timeout=None,
             require_embed=False, engine="mind"):
    selected = select_subset(
        instances, limit, seed, include_abstention)
    dataset_abstentions = sum(
        should_skip_abstention(instance, include_abstention)
        for instance in instances)
    root = Path(tempfile.mkdtemp(prefix="mind-longmemeval-"))
    metrics = {
        "evaluated": 0,
        "selected_questions": len(selected),
        "selected_question_ids": [
            str(instance.get("question_id", "unknown"))
            for instance in selected
        ],
        "evaluated_question_ids": [],
        "dataset_questions": len(instances),
        "skipped_no_evidence": 0,
        "skipped_abstention": dataset_abstentions,
        "skipped_abstention_scope": "dataset",
        "memory_records": 0,
        "evidence_at_1": 0,
        "evidence_at_k": 0,
        "answer_string_at_k": 0,
        "latencies_ms": [],
        "backend": {
            "configured": bool(embed_cmd or embed_server),
            "mode": "bm25" if engine == "bm25" else "offline",
            "models": [],
            "calls": 0,
            "fallbacks": 0,
            "fallback_reasons": {},
        },
    }
    try:
        with embedding_environment(
                embed_cmd, embed_server, embed_timeout):
            for index, instance in enumerate(selected):
                if engine == "bm25":
                    hippo = BM25Baseline()
                else:
                    question_root = root / ("%04d" % index)
                    question_root.mkdir()
                    hippo = Hippocampus(question_root / "graph.json")
                evidence_nodes, records = remember_instance(
                    instance, hippo, granularity)
                if not evidence_nodes:
                    metrics["skipped_no_evidence"] += 1
                    continue
                metrics["memory_records"] += records
                started = time.perf_counter()
                results, _, _ = hippo.recall(
                    instance.get("question", ""), top_k=top_k)
                metrics["latencies_ms"].append(
                    (time.perf_counter() - started) * 1000)
                if engine == "mind":
                    backend = hippo.reranker.last_report
                    metrics["backend"]["calls"] += backend["calls"]
                    if backend["backend"] in ("command", "server"):
                        metrics["backend"]["mode"] = backend["backend"]
                    if backend.get("model") and backend["model"] not in \
                            metrics["backend"]["models"]:
                        metrics["backend"]["models"].append(
                            backend["model"])
                    if backend["fallback"]:
                        metrics["backend"]["fallbacks"] += 1
                        reason = backend["reason"] or "unknown"
                        reasons = metrics["backend"]["fallback_reasons"]
                        reasons[reason] = reasons.get(reason, 0) + 1
                        if require_embed:
                            raise RuntimeError(
                                "embedding backend required but fell back: %s"
                                % reason)
                top_nodes = [node_id for node_id, _, _ in results]
                top_texts = [node["text"] for _, _, node in results]
                metrics["evaluated"] += 1
                metrics["evaluated_question_ids"].append(
                    str(instance.get("question_id", "unknown")))
                if top_nodes[:1] and top_nodes[0] in evidence_nodes:
                    metrics["evidence_at_1"] += 1
                if any(
                        node_id in evidence_nodes
                        for node_id in top_nodes[:top_k]):
                    metrics["evidence_at_k"] += 1
                if any(
                        answer_in_text(instance.get("answer"), text)
                        for text in top_texts[:top_k]):
                    metrics["answer_string_at_k"] += 1
    finally:
        shutil.rmtree(root, ignore_errors=True)
    evaluated = max(1, metrics["evaluated"])
    metrics["evidence_at_1_rate"] = (
        metrics["evidence_at_1"] / evaluated)
    metrics["evidence_at_k_rate"] = (
        metrics["evidence_at_k"] / evaluated)
    metrics["answer_string_at_k_rate"] = (
        metrics["answer_string_at_k"] / evaluated)
    metrics["median_latency_ms"] = (
        statistics.median(metrics["latencies_ms"])
        if metrics["latencies_ms"] else 0.0)
    metrics["p95_latency_ms"] = p95(metrics["latencies_ms"])
    metrics["avg_memory_records"] = (
        metrics["memory_records"] / metrics["evaluated"]
        if metrics["evaluated"] else 0.0)
    return metrics


def make_report(metrics, source, digest, args, manifest):
    manifest_path = Path(args.manifest).resolve() if args.manifest else None
    if manifest_path is not None:
        try:
            manifest_label = str(manifest_path.relative_to(ROOT))
        except ValueError:
            manifest_label = manifest_path.name
    else:
        manifest_label = None
    report = dict(metrics)
    report.update({
        "format": 1,
        "source": source,
        "dataset_sha256": digest,
        "dataset_revision": manifest.get("revision"),
        "manifest": manifest_label,
        "subset": {
            "limit": args.limit,
            "seed": args.seed,
            "top_k": args.top_k,
            "granularity": args.granularity,
            "include_abstention": args.include_abstention,
            "engine": args.engine,
        },
        "command": reproducible_command(),
        "provenance": repo_provenance((
            "bench/longmemeval.py",
            "bench/manifests/longmemeval.json",
        )),
    })
    return report


def print_report(report):
    subset = report["subset"]
    metrics = report
    provenance = report["provenance"]
    print("LongMemEval retrieval benchmark (mind)")
    print("=" * 56)
    print("source: %s" % report["source"])
    print("dataset revision: %s" % report["dataset_revision"])
    print("dataset sha256: %s" % report["dataset_sha256"])
    print("commit: %s%s" % (
        provenance["commit"],
        " (dirty)" if provenance["dirty"] else ""))
    print("mind.py sha256: %s" % provenance["mind_sha256"])
    print("backend: %s | calls %d | fallbacks %d | models %s" % (
        metrics["backend"]["mode"],
        metrics["backend"]["calls"],
        metrics["backend"]["fallbacks"],
        ",".join(metrics["backend"]["models"]) or "none"))
    print("command: %s" % report["command"])
    print("subset: limit=%s seed=%s granularity=%s top_k=%s" % (
        subset["limit"] or "all", subset["seed"],
        subset["granularity"], subset["top_k"]))
    print(
        "evaluated: %d | dataset abstentions excluded: %d | "
        "selected without evidence: %d" % (
            metrics["evaluated"], metrics["skipped_abstention"],
            metrics["skipped_no_evidence"]))
    print("memory records: %d total | %.1f avg/question" % (
        metrics["memory_records"], metrics["avg_memory_records"]))
    evidence_label = (
        "evidence-turn" if subset["granularity"] == "turn"
        else "evidence-session")
    print("%s@1 %.3f | %s@%d %.3f | answer-string@%d %.3f" % (
        evidence_label, metrics["evidence_at_1_rate"],
        evidence_label, subset["top_k"],
        metrics["evidence_at_k_rate"], subset["top_k"],
        metrics["answer_string_at_k_rate"]))
    print("latency median %.2f ms | p95 %.2f ms" % (
        metrics["median_latency_ms"], metrics["p95_latency_ms"]))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", default=str(DEFAULT_MANIFEST),
        help="Pinned dataset/subset manifest.")
    parser.add_argument(
        "--data", help="Local JSON file or immutable URL.")
    parser.add_argument(
        "--expected-sha256",
        help="Expected dataset digest; defaults to manifest digest.")
    parser.add_argument(
        "--cache-dir", default=".bench-cache",
        help="Verified download cache directory.")
    parser.add_argument(
        "--limit", type=positive_int,
        help="Number of non-abstention questions.")
    parser.add_argument("--seed", type=int, help="Deterministic subset seed.")
    parser.add_argument(
        "--top-k", type=positive_int,
        help="Recall cutoff for evidence@k.")
    parser.add_argument(
        "--granularity", choices=("turn", "session"))
    parser.add_argument(
        "--engine", choices=("mind", "bm25"), default="mind",
        help="Retrieval engine; bm25 is the dependency-free baseline.")
    parser.add_argument("--include-abstention", action="store_true")
    parser.add_argument(
        "--embed-cmd",
        help="Explicit mind-embed-v1 command; ambient variables are ignored.")
    parser.add_argument(
        "--embed-server",
        help=(
            "Explicit persistent mind-embed-server-v1 command; ambient "
            "variables are ignored."))
    parser.add_argument("--embed-timeout", type=float)
    parser.add_argument(
        "--require-embed", action="store_true",
        help="Abort instead of falling back to offline ranking.")
    parser.add_argument("--json-out")
    args = parser.parse_args(argv)
    if args.engine == "bm25" and (
            args.embed_cmd or args.embed_server or args.require_embed):
        parser.error(
            "--engine bm25 cannot be combined with embedding options")
    return args


def main(argv=None):
    args = parse_args(argv)
    manifest = load_manifest(args.manifest)
    subset = manifest["subset"]
    args.limit = args.limit or positive_int(subset.get("limit", 50))
    args.seed = args.seed if args.seed is not None else int(
        subset.get("seed", 13))
    args.top_k = args.top_k or positive_int(subset.get("top_k", 5))
    args.granularity = (
        args.granularity or subset.get("granularity", "turn"))
    if not args.include_abstention:
        args.include_abstention = bool(
            subset.get("include_abstention", False))
    source = args.data or manifest["url"]
    expected = (
        args.expected_sha256
        if args.expected_sha256 is not None
        else (manifest["sha256"] if args.data is None else None)
    )
    path, source_label = resolve_data(
        source, Path(args.cache_dir), expected)
    instances = load_instances(path)
    metrics = evaluate(
        instances,
        limit=args.limit,
        seed=args.seed,
        top_k=args.top_k,
        granularity=args.granularity,
        include_abstention=args.include_abstention,
        embed_cmd=args.embed_cmd,
        embed_server=args.embed_server,
        embed_timeout=args.embed_timeout,
        require_embed=args.require_embed,
        engine=args.engine,
    )
    digest = verify_digest(path, expected)
    report = make_report(
        metrics, source_label, digest, args, manifest)
    print_report(report)
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0 if metrics["evaluated"] else 1


if __name__ == "__main__":
    sys.exit(main())
