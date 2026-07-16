#!/usr/bin/env python3
"""Auto-first 30-session and five-year lifecycle acceptance simulation."""
import argparse
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import mind as M  # noqa: E402
from bench.provenance import (  # noqa: E402
    repo_provenance, reproducible_command)

START = datetime(2026, 1, 1, 9, 0, 0)


class isolated_environment:
    KEYS = (
        "MIND_AUTO_DREAM", "MIND_BY", "MIND_SESSION", "MIND_USER_HOME",
        "MIND_EMBED_CMD", "MIND_EMBED_SERVER",
    )

    def __init__(self, user_home):
        self.user_home = user_home
        self.previous = {}

    def __enter__(self):
        self.previous = {key: os.environ.get(key) for key in self.KEYS}
        os.environ["MIND_AUTO_DREAM"] = "1"
        os.environ["MIND_BY"] = "autonomy-benchmark"
        os.environ["MIND_USER_HOME"] = str(self.user_home)
        os.environ.pop("MIND_EMBED_CMD", None)
        os.environ.pop("MIND_EMBED_SERVER", None)

    def __exit__(self, *_):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def quiet_call(method, *args, **kwargs):
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        return method(*args, **kwargs)


def run_sessions(base):
    root = base / "sessions"
    root.mkdir()
    clock = [START]
    original_now = M._now
    M._now = lambda: clock[0]
    accepted = []
    rejected_secret = 0
    rejected_transient = 0
    try:
        mind = M.Mind(root)
        quiet_call(mind.init)
        for session in range(30):
            clock[0] = START + timedelta(days=session)
            os.environ["MIND_SESSION"] = "session-%02d" % session
            if session < 10:
                text = (
                    "production database persistence fact %02d uses "
                    "postgres records and transactional storage" % session)
            elif session == 10:
                text = (
                    "production database persistence uses mysql records "
                    "for the same transactional storage")
            else:
                text = (
                    "durable project convention %02d uses reviewed "
                    "configuration and deterministic tests" % session)
            if quiet_call(mind.capture, text) == "accepted":
                accepted.append(text)
            rejected_secret += quiet_call(
                mind.capture,
                "api_key = sk-example-autonomy-secret-%032d" % session,
            ) == "rejected"
            rejected_transient += quiet_call(
                mind.capture,
                "working on pull request %d and fixed bug today" % session,
            ) == "rejected"
        hippo = M.Hippocampus(
            root / M.MIND_DIR / M.GRAPH_FILE)
        cortex = M.Cortex(root / M.MIND_DIR / M.CORTEX_DIR)
        growth = M.Growth(
            root / M.MIND_DIR, hippo, cortex).digest(days=60)
        doctor = M.Doctor(
            root, hippo,
            M.Active(root / M.MIND_DIR, hippo, cortex)).run()
        serialized = json.dumps(
            hippo.nodes, ensure_ascii=False)
        conflict_edges = sum(
            edge.get("relation") == "possible-conflict"
            for neighbors in hippo.edges.values()
            for edge in neighbors.values()
        )
        return {
            "sessions": 30,
            "accepted": len(accepted),
            "rejected_secrets": rejected_secret,
            "rejected_transient": rejected_transient,
            "all_durable_present": all(
                text in serialized for text in accepted),
            "secret_absent": "sk-example-autonomy-secret" not in serialized,
            "transient_absent": "working on pull request" not in serialized,
            "cortex_topics": len(cortex.files()),
            "conflict_edges": conflict_edges,
            "dream_files": len(list(
                (root / M.MIND_DIR / M.DREAMS_DIR).glob("*.md"))),
            "growth_learned": growth["facts_learned"],
            "growth_matches_journal": (
                growth["facts_learned"] == len(accepted)),
            "doctor_ok": doctor["ok"],
        }
    finally:
        M._now = original_now


def _long_fact(day, payload_chars):
    prefix = (
        "durable simulated architecture fact day %04d component %04d "
        "records a stable compatibility observation " % (day, day % 97)
    )
    token = "evidence%04d " % day
    repetitions = max(
        1, (payload_chars - len(prefix)) // len(token))
    return (prefix + token * repetitions)[:payload_chars]


def run_horizon(base, days, payload_chars):
    root = base / "horizon"
    root.mkdir()
    clock = [START]
    original_now = M._now
    M._now = lambda: clock[0]
    try:
        mind = M.Mind(root)
        quiet_call(mind.init)
        for day in range(1, days + 1):
            clock[0] = START + timedelta(days=day)
            os.environ["MIND_SESSION"] = "horizon-%04d" % day
            result = quiet_call(
                mind.capture, _long_fact(day, payload_chars))
            if result != "accepted":
                raise AssertionError(
                    "durable horizon fact was not accepted")
            if day % 90 == 0:
                target = root / "AGENTS.md"
                payload = target.read_bytes()
                target.write_bytes(
                    b"\r\n".join(payload.splitlines()) + b"\r\n")
        mind._ensure()
        scheduler = M._read_scheduler_state(
            root / M.MIND_DIR)
        signal = root / M.MIND_DIR / M.SIGNALS_FILE
        archives = sorted(
            (root / M.MIND_DIR).glob("archive*.md"))
        guards = {}
        for target in M.Active.CANONICAL:
            text = (root / target).read_text("utf-8")
            guards[target] = text.count(M.Active.BEGIN)
        doctor = M.Doctor(
            root, mind.hippo, mind.active).run()
        return {
            "simulated_days": days,
            "simulated_years": days / 365.0,
            "payload_chars": payload_chars,
            "current_nodes": len(mind.hippo.nodes),
            "archive_files": len(archives),
            "archive_total_bytes": sum(
                path.stat().st_size for path in archives),
            "active_archive_bytes": (
                (root / M.MIND_DIR / "archive.md").stat().st_size
                if (root / M.MIND_DIR / "archive.md").exists() else 0),
            "signals_bytes": (
                signal.stat().st_size if signal.exists() else 0),
            "scheduler_pending": scheduler["pending"],
            "guard_counts": guards,
            "doctor_ok": doctor["ok"],
            "archive_rotated": any(
                path.name != "archive.md" for path in archives),
        }
    finally:
        M._now = original_now


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=1825)
    parser.add_argument("--payload-chars", type=int, default=5000)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--json-out")
    args = parser.parse_args(argv)
    if args.quick:
        args.days = 365
        args.payload_chars = 1000
    if args.days <= 0:
        parser.error("--days must be positive")
    if not (200 <= args.payload_chars <= M.MAX_TEXT_CHARS):
        parser.error("--payload-chars must be between 200 and MAX_TEXT_CHARS")

    base = Path(tempfile.mkdtemp(prefix="mind-autonomy-"))
    try:
        with isolated_environment(base / "user-memory"):
            sessions = run_sessions(base)
            horizon = run_horizon(
                base, args.days, args.payload_chars)
        report = {
            "benchmark": "auto-first-autonomy-v1",
            "command": reproducible_command(),
            "provenance": repo_provenance(("bench/autonomy.py",)),
            "sessions": sessions,
            "horizon": horizon,
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        sessions_ok = (
            sessions["accepted"] == 30
            and sessions["rejected_secrets"] == 30
            and sessions["rejected_transient"] == 30
            and sessions["all_durable_present"]
            and sessions["secret_absent"]
            and sessions["transient_absent"]
            and sessions["cortex_topics"] >= 1
            and sessions["conflict_edges"] >= 2
            and sessions["dream_files"] >= 1
            and sessions["growth_matches_journal"]
            and sessions["doctor_ok"]
        )
        horizon_ok = (
            horizon["signals_bytes"] <= M.MAX_SIGNALS_BYTES
            and horizon["scheduler_pending"] < M.AUTO_DREAM_SIGNALS
            and all(
                count == 1
                for count in horizon["guard_counts"].values())
            and horizon["active_archive_bytes"] <= M.ARCHIVE_ROTATE_BYTES
            and horizon["doctor_ok"]
        )
        if args.days >= 1825:
            horizon_ok = horizon_ok and horizon["archive_rotated"]
        return 0 if sessions_ok and horizon_ok else 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
