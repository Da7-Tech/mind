#!/usr/bin/env python3
"""mind soak test — 180 simulated days of realistic agent usage.

Answers the question unit tests cannot: does memory stay signal or decay
into noise over months? The simulation drives the REAL code (remember /
recall / bump / dream) through mind's injectable clock (`mind._now`) — no
simplified model, no mocked logic. Deterministic (seeded): rerun it and
you get the same numbers.

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
        ("اسم المستخدم رائف وهو مطور من الرياض", "ما اسم المستخدم", "رائف"),
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
    act = (tmp / M.MIND_DIR).parent  # ACTIVE lives in tmp/.mind? no: mind_dir=tmp
    act_text = (tmp / "ACTIVE.md").read_text("utf-8") if (tmp / "ACTIVE.md").exists() else \
        (tmp / M.ACTIVE_FILE).read_text("utf-8")
    core_markers = [m for facts in CORE.values() for _, _, m in facts]
    hot_lines = [ln for ln in act_text.splitlines() if ln.startswith("- ")]
    hot_core = sum(1 for ln in hot_lines if any(m in ln for m in core_markers))
    print("working memory: %d/%d hot slots are core facts"
          % (hot_core, len(hot_lines)))

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
    print("verdict: %s" % ("PASS" if all_ok else "FAIL"))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
