#!/usr/bin/env python3
"""Stdlib concept-hash reference for the mind-embed-server-v1 protocol."""
import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

DIMENSION = 1024
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
CONCEPT_GROUPS = {
    "authentication": (
        "authentication", "auth", "oauth", "login", "identity"),
    "backup": (
        "backup", "backups", "snapshot", "snapshots", "copies"),
    "browser-test": (
        "browser", "drives", "end", "playwright", "e2e", "workflow",
        "workflows"),
    "cache": (
        "accelerates", "cache", "caches", "caching", "redis",
        "repeated", "responses"),
    "cloud-host": (
        "cloud", "host", "hosts", "hosted", "hosting", "hetzner"),
    "database": (
        "database", "db", "postgres", "mysql", "sqlite", "records",
        "relational"),
    "deploy": (
        "deploy", "deployed", "deployment", "release", "releases",
        "rollout", "rolled", "blue", "green"),
    "dns": (
        "dns", "domain", "domains", "cloudflare"),
    "errors": (
        "error", "errors", "exception", "exceptions", "sentry"),
    "formatter": (
        "format", "formatter", "formatting", "ruff", "black", "style"),
    "frontend": (
        "frontend", "react", "web", "interface", "ui"),
    "infrastructure": (
        "creates", "infrastructure", "terraform", "provision", "provisions",
        "provisioned", "resources"),
    "location": (
        "where", "kept", "located", "stored", "host", "helsinki"),
    "mobile": (
        "mobile", "phone", "flutter"),
    "monitoring": (
        "monitor", "monitoring", "observability", "dashboard",
        "dashboards", "grafana", "panels"),
    "orm": (
        "orm", "sqlalchemy", "rows", "objects", "mapping", "maps"),
    "production": (
        "production", "live"),
    "queue": (
        "queue", "queues", "broker", "rabbitmq", "celery", "tasks",
        "jobs"),
    "secrets": (
        "secret", "secrets", "credential", "credentials", "environment",
        "variables", "vault"),
    "session": (
        "keeps", "session", "sessions", "state"),
    "source-control": (
        "source", "repository", "repo", "github", "code", "control"),
    "support": (
        "support", "conversation", "conversations", "intercom",
        "messages"),
}
CONCEPTS = {
    token: concept
    for concept, tokens in CONCEPT_GROUPS.items()
    for token in tokens
}
CONCEPT_WEIGHTS = {
    "authentication": 3.0,
    "backup": 3.0,
    "browser-test": 3.5,
    "cache": 3.5,
    "database": 3.5,
    "deploy": 3.0,
    "errors": 3.5,
    "formatter": 3.0,
    "infrastructure": 3.5,
    "location": 3.0,
    "orm": 3.0,
    "production": 1.0,
    "queue": 3.0,
    "secrets": 3.0,
    "session": 4.5,
    "source-control": 3.0,
    "support": 3.0,
}


def frame(payload):
    return str(len(payload)).encode("ascii") + b"\n" + payload


def read_frame(stream):
    header = stream.readline()
    if not header:
        return None
    size = int(header.strip())
    if size < 0 or size > 10_000_000:
        raise ValueError("invalid frame size")
    payload = stream.read(size)
    if len(payload) != size:
        raise EOFError("truncated frame")
    return json.loads(payload.decode("utf-8"))


def write_frame(stream, value):
    payload = json.dumps(
        value, separators=(",", ":")).encode("utf-8")
    stream.write(frame(payload))
    stream.flush()


def vector(text):
    values = [0.0] * DIMENSION
    tokens = TOKEN_RE.findall((text or "").lower())
    concepts = [CONCEPTS.get(token, token) for token in tokens]
    features = [(token, 0.25) for token in tokens]
    features.extend(
        (concept, CONCEPT_WEIGHTS.get(concept, 2.0))
        for concept in concepts)
    features.extend(
        ("%s %s" % pair, 1.25)
        for pair in zip(concepts, concepts[1:]))
    for feature, weight in features:
        digest = hashlib.blake2b(
            feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % DIMENSION
        values[index] += weight if digest[4] & 1 == 0 else -weight
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--counter")
    args = parser.parse_args(argv)
    if args.counter:
        counter = Path(args.counter)
        current = int(counter.read_text("utf-8")) if counter.exists() else 0
        counter.write_text(str(current + 1), encoding="utf-8")
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        request = read_frame(stdin)
        if request is None:
            break
        if request.get("op") == "handshake":
            write_frame(stdout, {
                "protocol": "mind-embed-server-v1",
                "model_id": "stdlib-concept-hash",
                "revision": "2",
                "dimension": DIMENSION,
            })
        elif request.get("op") == "embed":
            texts = request.get("texts")
            if not isinstance(texts, list) or not all(
                    isinstance(text, str) for text in texts):
                write_frame(stdout, {"error": "texts must be strings"})
                continue
            write_frame(stdout, {
                "protocol": "mind-embed-server-v1",
                "vectors": [vector(text) for text in texts],
            })
        else:
            write_frame(stdout, {"error": "unknown operation"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
