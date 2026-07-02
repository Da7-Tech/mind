#!/usr/bin/env python3
"""mind multilingual benchmark — measured, reproducible, zero dependencies.

English + Arabic are the engineered languages (bench/bench.py, 20 queries).
This benchmark measures everything ELSE: languages mind was never tuned
for, split by script family:

  - space-separated scripts (French, German, Spanish, Russian, Turkish):
    whole-word indexing + IDF + char-n-gram fallback carry them with no
    per-language work
  - no-space scripts (Chinese, Japanese, Korean): character-bigram
    tokenization (added in 5.6.0 — recall@1 was 3/6 for CJK before it)

Each language: 3 facts with known answers, recalled against distractor
noise. CI gate: every language >= 2/3 AND overall >= 0.9. Deterministic.

Run:  python3 bench/multilang.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mind import Hippocampus  # noqa: E402

CASES = {
    "french": [
        ("la base de données du projet est postgres seize",
         "quelle base de données utilisons-nous", "postgres"),
        ("le serveur principal est à francfort",
         "où est le serveur principal", "francfort"),
        ("la limite de requêtes est cent par minute",
         "quelle est la limite de requêtes", "cent"),
    ],
    "german": [
        ("die projektdatenbank ist postgres sechzehn",
         "welche datenbank verwenden wir", "postgres"),
        ("der hauptserver steht in frankfurt",
         "wo steht der hauptserver", "frankfurt"),
        ("das deployment läuft über docker compose",
         "wie läuft das deployment", "docker"),
    ],
    "spanish": [
        ("la base de datos del proyecto es postgres dieciséis",
         "qué base de datos usamos", "postgres"),
        ("el servidor principal está en frankfurt",
         "dónde está el servidor principal", "frankfurt"),
        ("el límite es cien peticiones por minuto",
         "cuál es el límite de peticiones", "cien"),
    ],
    "russian": [
        ("база данных проекта это postgres шестнадцать",
         "какая база данных у проекта", "postgres"),
        ("основной сервер находится во франкфурте",
         "где находится основной сервер", "франкфурт"),
        ("лимит запросов сто в минуту",
         "какой лимит запросов", "сто"),
    ],
    "turkish": [
        ("proje veritabanı postgres onaltı sürümüdür",
         "hangi veritabanını kullanıyoruz", "postgres"),
        ("ana sunucu frankfurt şehrinde bulunuyor",
         "ana sunucu nerede", "frankfurt"),
        ("istek limiti dakikada yüz adettir",
         "istek limiti nedir", "yüz"),
    ],
    "chinese": [
        ("项目数据库是postgres十六版本", "我们用什么数据库", "postgres"),
        ("主服务器位于法兰克福机房", "主服务器在哪里", "法兰克福"),
        ("请求限制是每分钟一百次", "请求限制是多少", "一百"),
    ],
    "japanese": [
        ("プロジェクトのデータベースはpostgres十六です",
         "データベースは何ですか", "postgres"),
        ("メインサーバーはフランクフルトにあります",
         "メインサーバーはどこですか", "フランクフルト"),
        ("リクエスト制限は毎分百件です",
         "リクエスト制限はいくつですか", "百"),
    ],
    "korean": [
        ("프로젝트 데이터베이스는 postgres 십육 버전이다",
         "우리는 어떤 데이터베이스를 사용하나요", "postgres"),
        ("메인 서버는 프랑크푸르트에 있다",
         "메인 서버는 어디에 있나요", "프랑크푸르트"),
        ("요청 제한은 분당 백 건이다",
         "요청 제한은 얼마인가요", "백"),
    ],
}

# distractor noise in every script family, so each graph is not a toy
DISTRACTORS = ["réunion notes numéro %d", "meeting draft nummer %d",
               "заметка номер %d", "nota borrador número %d",
               "会议记录第%d号", "メモ番号%d", "회의 메모 %d번",
               "toplantı notu %d", "misc scratch note %d"]


def main():
    print("mind multilingual benchmark (untuned languages)")
    print("=" * 56)
    total_hits = total_q = 0
    ok = True
    for lang, cases in CASES.items():
        tmp = Path(tempfile.mkdtemp(prefix="mind-ml-"))
        h = Hippocampus(tmp / "graph.json")
        for fact, _, _ in cases:
            h.remember(fact)
        for i, d in enumerate(DISTRACTORS * 4):
            h.remember(d % i)
        hits = 0
        misses = []
        for fact, q, marker in cases:
            results, _, _ = h.recall(q)
            if results and marker in results[0][2]["text"]:
                hits += 1
            else:
                misses.append(q)
        total_hits += hits
        total_q += len(cases)
        if hits < 2:                      # per-language gate: >= 2/3
            ok = False
        print("%-10s recall@1 %d/%d %s"
              % (lang, hits, len(cases),
                 ("   MISS: " + "; ".join(misses)) if misses else ""))
    rate = total_hits / total_q
    if rate < 0.9:                        # overall gate
        ok = False
    print("-" * 56)
    print("overall recall@1: %d/%d (%.2f)" % (total_hits, total_q, rate))
    print("verdict: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
