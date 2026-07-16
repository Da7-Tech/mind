#!/usr/bin/env python3
"""Small deterministic paraphrase/rewording benchmark for semantic backends."""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mind import CommandEmbed, HashEmbed  # noqa: E402
from bench.provenance import (  # noqa: E402
    repo_provenance, reproducible_command)

CASES = [
    ("what database runs the live service",
     "postgres persists records for production",
     "the live service deploys with blue green releases"),
    ("where do backup copies live",
     "snapshots are kept on the helsinki host",
     "backup failures appear in sentry alerts"),
    ("what provides login identity",
     "oauth handles authentication",
     "user profiles are stored in postgres"),
    ("how are releases rolled out",
     "deployment uses blue green",
     "release notes are stored in github"),
    ("what accelerates repeated api reads",
     "redis caches responses",
     "api reads require oauth authentication"),
    ("which utility rewrites python style",
     "ruff format is the formatter",
     "python dependencies are locked with uv"),
    ("where do application exceptions go",
     "sentry receives error reports",
     "application metrics go to prometheus"),
    ("what broker carries background tasks",
     "rabbitmq queues celery jobs",
     "background logs are stored in loki"),
    ("who hosts the live environment",
     "hetzner cloud runs production",
     "the environment is provisioned by terraform"),
    ("what creates cloud resources",
     "terraform provisions infrastructure",
     "cloud dashboards are shown in grafana"),
    ("which library builds the web interface",
     "react powers the frontend",
     "the web api is served by fastapi"),
    ("what drives end to end web checks",
     "playwright automates browser tests",
     "unit checks run with pytest"),
    ("where are customer conversations managed",
     "intercom handles support messages",
     "customer analytics are recorded by mixpanel"),
    ("what keeps login state between requests",
     "redis stores server sessions",
     "login events are sent to sentry"),
    ("how are production credentials injected",
     "secrets come from environment variables",
     "production feature flags live in postgres"),
    ("what displays observability panels",
     "grafana renders monitoring dashboards",
     "observability logs are collected by loki"),
    ("who manages domain name records",
     "cloudflare controls dns",
     "domain deployments are hosted by vercel"),
    ("what maps relational rows to objects",
     "sqlalchemy is the database orm",
     "objects are validated by pydantic"),
    ("where is the code repository hosted",
     "github stores source control",
     "code builds run in jenkins"),
    ("which toolkit ships the phone application",
     "flutter builds the mobile app",
     "the application website uses react"),
]


def run(server_cmd=None):
    previous = os.environ.get("MIND_EMBED_SERVER")
    if server_cmd:
        os.environ["MIND_EMBED_SERVER"] = server_cmd
    else:
        os.environ.pop("MIND_EMBED_SERVER", None)
    embedder = CommandEmbed(fallback=HashEmbed(), project_root=ROOT)
    correct = 0
    latencies = []
    try:
        for query, answer, distractor in CASES:
            started = time.perf_counter()
            scores = embedder.similarities(
                query, [answer, distractor])
            latencies.append(
                (time.perf_counter() - started) * 1000)
            correct += scores[0] > scores[1]
    finally:
        embedder.close()
        if previous is None:
            os.environ.pop("MIND_EMBED_SERVER", None)
        else:
            os.environ["MIND_EMBED_SERVER"] = previous
    return {
        "cases": len(CASES),
        "correct": correct,
        "accuracy": correct / len(CASES),
        "cold_latency_ms": latencies[0],
        "warm_median_latency_ms": statistics.median(latencies[1:]),
        "p95_latency_ms": sorted(latencies)[
            max(0, int(len(latencies) * 0.95) - 1)],
        "backend": "server" if server_cmd else "offline",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-cmd")
    parser.add_argument("--json-out")
    args = parser.parse_args(argv)
    offline = run()
    report = {
        "benchmark": "paraphrase-ranking-v1",
        "command": reproducible_command(),
        "provenance": repo_provenance((
            "bench/paraphrase.py",
            "contrib/concept_embed_server.py",
        )),
        "offline": offline,
    }
    if args.server_cmd:
        server = run(args.server_cmd)
        report["server"] = server
        report["accuracy_gain"] = (
            server["accuracy"] - offline["accuracy"])
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
