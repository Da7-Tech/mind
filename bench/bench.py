#!/usr/bin/env python3
"""mind benchmark — measured, reproducible, zero dependencies.

Builds a synthetic bilingual (EN + AR) memory of known facts plus
distractors, then measures:
  - recall@1 / recall@5 on 20 natural-language queries with known answers
  - median / p95 recall latency at 100 and 1,000 nodes
  - dream determinism (two runs on identical state -> identical plan)

Run:  python3 bench/bench.py
Numbers in README.md come from this script — re-run it yourself.
"""
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mind import Hippocampus, Cortex, Dreamer  # noqa: E402

random.seed(42)

FACTS = [
    ("the project database is postgres 16 with prisma orm", "which database do we use", "postgres"),
    ("frontend built with react and typescript strict mode", "what is the frontend stack", "react"),
    ("deploy target is a hetzner vps using docker compose", "where do we deploy", "hetzner"),
    ("api authentication uses bearer tokens with 24h expiry", "how does api auth work", "bearer"),
    ("payment provider is stripe with 2 percent fees", "which payment provider", "stripe"),
    ("the boss's name is khaled and he reviews every friday", "who reviews the code", "khaled"),
    ("rate limit is 100 requests per minute per client", "what is the rate limit", "100"),
    ("staging environment lives at staging.example.com", "what is the staging url", "staging"),
    ("error tracking goes to sentry project souq-prod", "where do errors go", "sentry"),
    ("the mobile app is flutter targeting ios and android", "what framework for mobile", "flutter"),
    ("اسم المستخدم رائف وهو مطور من الرياض", "ما اسم المستخدم", "رائف"),
    ("المشروع يستخدم قاعدة بيانات سيكلايت للتخزين المحلي", "ما قاعدة البيانات المحلية", "سيكلايت"),
    ("الخادم الرئيسي في فرانكفورت والنسخ الاحتياطي في هلسنكي", "أين الخادم الرئيسي", "فرانكفورت"),
    ("لغة الواجهة العربية أولًا مع دعم الإنجليزية", "ما لغة الواجهة", "العربية"),
    ("ميزانية الاستضافة الشهرية عشرون دولارًا", "كم ميزانية الاستضافة", "عشرون"),
    ("code style is black with line length 100", "what code formatter", "black"),
    ("release cadence is every second tuesday", "when do we release", "tuesday"),
    ("the design system uses tailwind with a custom palette", "what css framework", "tailwind"),
    ("customer support handled through intercom", "what tool for support", "intercom"),
    ("logs are shipped to grafana loki retention 30 days", "where are the logs", "loki"),
]

DISTRACTOR_WORDS = ("meeting", "notes", "random", "idea", "draft", "todo",
                    "misc", "temp", "thought", "aside", "فكرة", "ملاحظة",
                    "مسودة", "اجتماع", "عشوائي")


def build(n_distractors):
    tmp = Path(tempfile.mkdtemp(prefix="mind-bench-"))
    h = Hippocampus(tmp / "graph.json")
    for fact, _, _ in FACTS:
        h.remember(fact)
    for i in range(n_distractors):
        w = DISTRACTOR_WORDS[i % len(DISTRACTOR_WORDS)]
        h.remember("%s %d some filler content nobody asks about %d" % (w, i, i))
    return tmp, h


def run_recall(h):
    hits1 = hits5 = 0
    lat = []
    for fact, query, marker in FACTS:
        t0 = time.perf_counter()
        results, _, _ = h.recall(query)
        lat.append((time.perf_counter() - t0) * 1000)
        texts = [r[2]["text"] for r in results]
        if texts and marker in texts[0]:
            hits1 += 1
        if any(marker in t for t in texts):
            hits5 += 1
    return hits1 / len(FACTS), hits5 / len(FACTS), lat


def dream_determinism():
    t1, h1 = build(30)
    t2, h2 = build(30)
    d1 = Dreamer(t1, h1, Cortex(t1 / "cortex")).dream(dry_run=True)[1]
    d2 = Dreamer(t2, h2, Cortex(t2 / "cortex")).dream(dry_run=True)[1]
    # strip timestamps before comparing
    import re
    clean = lambda s: re.sub(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}", "", s)
    same = clean(d1) == clean(d2)
    shutil.rmtree(t1, ignore_errors=True)
    shutil.rmtree(t2, ignore_errors=True)
    return same


def main():
    print("mind benchmark (python %s)" % sys.version.split()[0])
    print("=" * 56)
    ok = True
    for n_distract, label in ((80, "100 nodes"), (980, "1,000 nodes")):
        tmp, h = build(n_distract)
        r1, r5, lat = run_recall(h)
        print("%-12s recall@1 %.2f | recall@5 %.2f | "
              "median %.2f ms | p95 %.2f ms"
              % (label, r1, r5, statistics.median(lat),
                 sorted(lat)[int(len(lat) * 0.95) - 1]))
        if r1 < 0.9:            # regression gate for CI
            ok = False
        shutil.rmtree(tmp, ignore_errors=True)
    det = dream_determinism()
    print("dream determinism: %s"
          % ("PASS (identical plan on identical state)" if det else "FAIL"))
    return 0 if (ok and det) else 1


if __name__ == "__main__":
    sys.exit(main())
