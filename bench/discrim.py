#!/usr/bin/env python3
"""mind discrimination benchmark — competing distractors, not clean noise.

bench.py measures needle-in-a-haystack recall against lexically ISOLATED
noise. Two independent audits pointed out (correctly) that 1.00 there
says nothing about telling apart facts that SHARE vocabulary. This
benchmark closes that gap: every query has at least one distractor that
overlaps it lexically — including the exact failure cases those audits
reproduced (identity questions vs "file name must match the class name").

Scoring is recall@1 against the marker. The gate is deliberately honest:
>= 0.85 overall. If a change drops discrimination, this fails in CI.

Run:  python3 bench/discrim.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mind import Hippocampus  # noqa: E402

# (facts[], query, marker-of-the-right-answer)
CASES = [
    # identity vs "name"-noun distractors (the audits' exact scenarios)
    (["file name must match the class name",
      "the env var name is DATABASE_URL",
      "my name is khaled and I live in riyadh"],
     "what is my name", "khaled"),
    (["اسم الملف يجب ان يطابق اسم الصنف",
      "اسمي داحم وأعمل من الرياض"],
     "ما اسمي", "داحم"),
    (["my name is khaled from riyadh",
      "اعمل على تحسين الاداء في المشروع"],
     "ما اسمي", "khaled"),
    # same entity, competing attributes
    (["the staging database is mysql five",
      "the production database is postgres sixteen"],
     "what is the production database", "postgres"),
    (["the staging database is mysql five",
      "the production database is postgres sixteen"],
     "what is the staging database", "mysql"),
    # shared subject, different predicates
    (["the api rate limit for free users is ten per minute",
      "the api rate limit for paid users is one hundred per minute"],
     "what is the rate limit for paid users", "hundred"),
    (["deploy to staging happens on every merge",
      "deploy to production happens every second tuesday"],
     "when do we deploy to production", "tuesday"),
    # overlapping tech vocabulary
    (["redis is used for session caching",
      "redis streams are used for the event queue"],
     "what do we use redis streams for", "queue"),
    (["the backup server lives in helsinki",
      "the main server lives in frankfurt"],
     "where is the backup server", "helsinki"),
    (["الخادم الرئيسي في فرانكفورت",
      "الخادم الاحتياطي في هلسنكي"],
     "أين الخادم الاحتياطي", "هلسنكي"),
    # config-key lookalikes
    (["the timeout for http requests is thirty seconds",
      "the timeout for database queries is five seconds"],
     "what is the database query timeout", "five"),
    (["error logs go to sentry",
      "access logs go to grafana loki"],
     "where do access logs go", "loki"),
]


def main():
    print("mind discrimination benchmark (competing distractors)")
    print("=" * 58)
    hits = 0
    for facts, q, marker in CASES:
        h = Hippocampus(Path(tempfile.mkdtemp(prefix="mind-dx-")) / "g.json")
        for f in facts:
            h.remember(f)
        r, _, _ = h.recall(q)
        ok = bool(r) and marker in r[0][2]["text"]
        hits += ok
        print("%s  %s" % ("PASS" if ok else "MISS", q))
        if not ok and r:
            print("      got: %s" % r[0][2]["text"][:60])
    rate = hits / len(CASES)
    print("-" * 58)
    print("discrimination recall@1: %d/%d (%.2f)" % (hits, len(CASES), rate))
    ok = rate >= 0.85
    print("verdict: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
