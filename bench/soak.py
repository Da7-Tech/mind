#!/usr/bin/env python3
"""mind soak test — 180 simulated days of realistic agent usage.

Answers the question unit tests cannot: does memory stay signal or decay
into noise over months? The simulation drives the REAL consolidation code
(remember / recall / bump / dream) through mind's injectable clock
(`mind._now`) — no simplified model, no mocked logic. It exercises the
in-process object API rather than the argv CLI dispatcher, so a final
smoke leg (see cli_smoke() below) shells out to `python3 mind.py ...` to
also cover the disk-reload + argv path that users actually reach.
Deterministic (seeded): rerun it and you get the same numbers.

Workload per simulated day:
  - 1-3 trivia notes remembered (90% never mentioned again — the noise)
  - core facts recalled on realistic schedules (daily / weekly / monthly),
    reinforced via bump() only when recall actually returns them (as the
    exported agent instructions specify)
  - a dream cycle every night

Measured at day 180:
  - core-fact survival + recall@1 per tier (daily/weekly/monthly)
  - trivia survival rate (the noise floor)
  - share of working memory (ACTIVE.md top slots) held by core facts
  - graph size trajectory (day 30/90/180) — bounded or runaway?
  - recall latency on the aged graph

Run:  python3 bench/soak.py
"""
import random
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mind as M                                    # noqa: E402

random.seed(7)

DAYS = 180
START = datetime(2026, 1, 1, 9, 0, 0)

CORE = {
    "daily": [
        ("the project database is postgres 16 with prisma", "which database", "postgres"),
        ("api auth uses bearer tokens with 24h expiry", "how does api auth work", "bearer"),
        ("deploy target is hetzner via docker compose", "where do we deploy", "hetzner"),
        ("اسم المستخدم خالد وهو مطور من الرياض", "ما اسم المستخدم", "خالد"),
        ("the main branch policy is squash merge only", "what is the merge policy", "squash"),
    ],
    "weekly": [
        ("rate limit is 100 requests per minute", "what is the rate limit", "100"),
        ("error tracking goes to sentry project souq-prod", "where do errors go", "sentry"),
        ("release cadence is every second tuesday", "when do we release", "tuesday"),
        ("staging environment lives at staging.example.com", "staging url", "staging"),
        ("ميزانية الاستضافة الشهرية عشرون دولارًا", "كم ميزانية الاستضافة", "عشرون"),
    ],
    "monthly": [
        ("the dns registrar is cloudflare with 2fa on the shared vault", "who is the dns registrar", "cloudflare"),
        ("backup restore drill procedure lives in runbooks slash restore", "where is the restore procedure", "runbook"),
        ("the invoice numbering scheme starts at 1000 per fiscal year", "invoice numbering scheme", "1000"),
        ("legacy ftp ingest is deprecated but kept for client acme", "why is ftp ingest kept", "acme"),
        ("the office wifi guest password rotates every quarter", "wifi guest password rotation", "quarter"),
    ],
}
TRIVIA_WORDS = ("meeting", "scratch", "random", "draft", "todo", "aside",
                "فكرة", "ملاحظة", "مسودة", "خاطرة", "temp", "misc")

RECALL_GAP = {"daily": (1, 3), "weekly": (6, 9), "monthly": (26, 34)}


def main():
    t_start = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="mind-soak-"))
    sim_clock = [START]
    M._now = lambda: sim_clock[0]          # inject the clock into the real code

    hippo = M.Hippocampus(tmp / "graph.json")
    cortex = M.Cortex(tmp / "cortex")
    dreamer = M.Dreamer(tmp, hippo, cortex)
    active = M.Active(tmp, hippo, cortex)

    next_recall = {}
    for tier, facts in CORE.items():
        for fact, q, marker in facts:
            hippo.remember(fact)
            next_recall[fact] = random.randint(*RECALL_GAP[tier])

    trivia_added, trivia_texts = 0, []
    sizes = {}

    for day in range(1, DAYS + 1):
        sim_clock[0] = START + timedelta(days=day, hours=random.randint(0, 8))

        # trivia churn
        for _ in range(random.randint(1, 3)):
            w = random.choice(TRIVIA_WORDS)
            text = "%s note %d about nothing lasting %d" % (w, trivia_added, day)
            hippo.remember(text)
            trivia_texts.append((day, text))
            trivia_added += 1

        # scheduled core recalls + reinforcement on confirmed hits
        for tier, facts in CORE.items():
            for fact, q, marker in facts:
                next_recall[fact] -= 1
                if next_recall[fact] <= 0:
                    results, _, _ = hippo.recall(q)
                    hit = [nid for nid, _, n in results if marker in n["text"]]
                    if hit:
                        # same call the agent-facing `confirm` CLI performs
                        hippo.bump(hit[:1])
                    next_recall[fact] = random.randint(*RECALL_GAP[tier])

        # nightly dream (the real consolidation path, journal and all)
        dreamer.dream()
        if day in (30, 90, 180):
            sizes[day] = len(hippo.nodes)

    # ── day-180 metrics ──────────────────────────────────────────
    print("mind soak — %d simulated days (seeded, real code, injected clock)"
          % DAYS)
    print("=" * 64)
    all_ok = True
    for tier, facts in CORE.items():
        alive = r1 = 0
        for fact, q, marker in facts:
            if any(marker in n["text"] for n in hippo.nodes.values()):
                alive += 1
            results, _, _ = hippo.recall(q)
            if results and marker in results[0][2]["text"]:
                r1 += 1
        print("core/%-8s survival %d/%d   recall@1 %d/%d"
              % (tier, alive, len(facts), r1, len(facts)))
        if alive < len(facts) or r1 < len(facts) - 1:
            all_ok = False

    texts_alive = {n["text"] for n in hippo.nodes.values()}
    trivia_alive = sum(1 for _, t in trivia_texts if t in texts_alive)
    stale = [(d, t) for d, t in trivia_texts if DAYS - d > 50]
    stale_alive = sum(1 for _, t in stale if t in texts_alive)
    print("trivia noise: %d added, %d survive overall; "
          "stale (>50d old): %d/%d survive"
          % (trivia_added, trivia_alive, stale_alive, len(stale)))
    # the gate: junk older than the grace window must be gone
    if stale and stale_alive / len(stale) > 0.02:
        all_ok = False

    active.generate(tmp)
    act_text = (tmp / M.ACTIVE_FILE).read_text("utf-8")
    core_markers = [m for facts in CORE.values() for _, _, m in facts]
    # count only the "Hot memories" section — instruction bullets also
    # start with "- " and would understate the core-fact share
    hot_section = act_text.split("## Hot memories")[1].split("##")[0]
    hot_lines = [ln for ln in hot_section.splitlines() if ln.startswith("- ")]
    hot_core = sum(1 for ln in hot_lines if any(m in ln for m in core_markers))
    print("working memory: %d/%d hot slots are core facts"
          % (hot_core, len(hot_lines)))
    if hot_core < 7:
        all_ok = False

    lat = []
    for tier, facts in CORE.items():
        for _, q, _ in facts:
            t0 = time.perf_counter()
            hippo.recall(q)
            lat.append((time.perf_counter() - t0) * 1000)
    print("graph size: day30 %d | day90 %d | day180 %d nodes (bounded: %s)"
          % (sizes[30], sizes[90], sizes[180],
             "yes" if sizes[180] < sizes[30] * 3 else "NO"))
    print("recall latency on aged graph: median %.2f ms" % statistics.median(lat))
    print("wall time: %.1f s" % (time.time() - t_start))
    shutil.rmtree(tmp, ignore_errors=True)

    cli_ok = cli_smoke()
    print("cli smoke (argv + disk-reload path): %s" % ("PASS" if cli_ok else "FAIL"))

    ok = all_ok and cli_ok
    print("verdict: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def cli_smoke():
    """Cover the path the object-API soak skips: the argv dispatcher and the
    disk reload that every real `python3 mind.py <cmd>` invocation performs
    (a separate process, reloading graph.json each time). Exercises
    init/remember/recall/confirm/correct/link/dream/export/status end to end.
    """
    import subprocess
    here = Path(__file__).resolve().parent.parent / "mind.py"
    proj = Path(tempfile.mkdtemp(prefix="mind-cli-smoke-"))
    def run(*args):
        return subprocess.run([sys.executable, str(here), *args], cwd=str(proj),
                              capture_output=True, text=True)
    try:
        assert run("init").returncode == 0
        assert run("remember", "the deploy target is hetzner via docker").returncode == 0
        assert run("remember", "the database is postgres 16").returncode == 0
        r = run("recall", "where do we deploy")
        assert r.returncode == 0 and "hetzner" in r.stdout
        import re as _re
        m = _re.search(r"id ([0-9a-f]{12})", r.stdout)
        assert m and run("confirm", m.group(1)).returncode == 0
        assert run("link", "the database is postgres 16",
                   "the deploy target is hetzner via docker", "used-by").returncode == 0
        assert run("correct", "database postgres", "the database is postgres 17").returncode == 0
        assert run("dream").returncode == 0
        assert run("export").returncode == 0
        s = run("status")
        assert s.returncode == 0 and "nodes:" in s.stdout
        # the corrected fact must survive a fresh process (disk reload)
        r2 = run("recall", "which database")
        assert r2.returncode == 0 and "postgres 17" in r2.stdout
        return True
    except AssertionError:
        return False
    finally:
        shutil.rmtree(proj, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
