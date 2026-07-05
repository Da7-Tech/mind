#!/usr/bin/env python3
"""
mind — brain-like memory for any coding agent.

Three layers (working / hippocampus / cortex) + spreading-activation recall
+ Ebbinghaus forgetting + dream consolidation between sessions + export to
common agent rule files (AGENTS.md / CLAUDE.md / GEMINI.md / tool-specific
dotfiles). One file. Zero dependencies.
Fully offline. Engineered for English + Arabic; measured on 10
languages (script-aware tokenizer: whole words for spaced scripts,
character bigrams for CJK/kana/Hangul/Thai).

Usage: python3 mind.py <command> [args]
  init                 create .mind/ in the current project
  remember "text"      add a memory node to the graph
  link "a" "b" [rel]   connect two memories with a weighted edge
  recall "question"    spreading-activation recall (RRF + IDF fusion)
  confirm <id> [...]   reinforce memories that actually answered you
  correct "old" "new"  supersede a wrong fact (transition kept, provenance logged)
  why <id>             provenance: origin, validity, full event history
  entity "term"        every fact about a term, current and superseded
  dream [--dry-run]    run the sleep cycle (light -> deep -> REM)
  export               regenerate agent rule files
  status               memory health report

Design: docs/DESIGN.md  |  License: MIT  |  https://github.com/Da7-Tech/mind
"""
import sys, os, json, re, time, math, hashlib
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter, defaultdict

__version__ = "6.2.5"

# ────────────────────────────────────────────────────────────────
# Tunables (see docs/DESIGN.md for the reasoning behind each value)
# ────────────────────────────────────────────────────────────────
MIND_DIR = ".mind"
GRAPH_FILE = "graph.json"
ACTIVE_FILE = "ACTIVE.md"
CORTEX_DIR = "cortex"
DREAMS_DIR = "dreams"
SIGNALS_FILE = "signals.jsonl"
JOURNAL_FILE = "journal.jsonl"  # append-only provenance log — NEVER cleared
#   (signals.jsonl is session telemetry and is cleared by dream; the
#    journal is the permanent answer to "where did this fact come from")

BOOST_PER_ACCESS = 0.15     # weight boost on confirmed recall (bump)
WEIGHT_THRESHOLD = 0.1      # nodes below this are pruned during dreams
RECALL_RADIUS = 3           # spreading-activation hop limit (cheap, local)
RECALL_TOP_K = 5
ACTIVATION_DECAY = 0.5      # activation halves at each hop
SPREADING_THRESHOLD = 0.05  # do not propagate activation below this
PROMOTION_THRESHOLD = 3     # cluster of >= 3 related nodes -> cortex
ACTIVE_TOKEN_BUDGET = 800   # working-memory budget in characters (~200 tokens)
STABILITY_BASE_DAYS = 3.0   # Ebbinghaus: base memory stability
STABILITY_PER_ACCESS = 14.0  # one confirmed recall buys ~two weeks of stability
_META_KEYS = frozenset({"last_edge_decay"})  # graph.json meta whitelist
AUTO_DREAM_SIGNALS = 10     # pending write signals that trigger an auto-dream
AUTO_DREAM_HOURS = 24       # ...or last dream older than this (with >=1 signal)
GRACE_DAYS = 45             # no memory dies within 45 days of its last access
#   (soak-test finding: monthly-cadence facts have recall gaps up to ~34
#    days; a 30-day grace lost them to the nightly dream one day before
#    their first recall. Weight still decays during grace, so unproven
#    memories fade from ACTIVE.md and rankings — they just aren't deleted.
#    Facts needed less often than ~every 6 weeks are a documented limit.)
EDGE_PRUNE_THRESHOLD = 0.1  # edges below this are pruned during dreams
EDGE_DECAY_PER_DREAM = 0.95  # every dream weakens every edge slightly...
EDGE_BOOST = 0.25            # ...and confirming either endpoint restrengthens
#   its edges. An edge whose endpoints never earn a confirmed recall decays
#   below the prune threshold after ~45 nightly dreams — the same horizon as
#   node grace. (Auditor finding: edge weights previously never changed, so
#   "synaptic pruning" was unreachable dead code for link edges.)
CLUSTER_SIM = 0.45          # similarity gate for dream clustering
SEPARATION_SIM = 0.92       # near-identical results are diversified in top-k
FUZZY_ACTIVATION = 0.5      # activation given to pattern-completion matches
MAX_TEXT_CHARS = 10000      # one memory is a fact, not a document: a cap
#   keeps graph.json / journal / ACTIVE processing sane (auditor finding:
#   nothing protected the tool from a single multi-megabyte "memory")


def _now():
    return datetime.now()


def _reject_symlinked_parents(path, boundary):
    """Raise if any directory from `path`'s parent up to (and including)
    `boundary` is a symlink. Walked on the RAW (unresolved) paths so the
    symlink itself is caught. Without this, os.replace() follows a symlinked
    parent dir and a write escapes the trust boundary (auditor finding: a
    symlinked .mind/dreams or .mind/cortex let dream/promote overwrite files
    outside the project)."""
    boundary = os.path.abspath(str(boundary))
    p = os.path.abspath(str(Path(path).parent))
    while True:
        if os.path.islink(p):
            raise ValueError("refusing to write through a symlinked parent: %s" % p)
        if p == boundary:
            break
        parent = os.path.dirname(p)
        if parent == p:
            # reached filesystem root WITHOUT crossing the boundary: the
            # path is not inside the trust boundary at all — refuse
            # instead of silently passing (auditor finding: the boundary
            # argument promised a containment check it never performed)
            raise ValueError("path %s escapes the trust boundary %s"
                             % (path, boundary))
        p = parent


def _read_text_retry(path):
    """Read a file that a concurrent writer may be os.replace-ing this
    very instant: Windows raises transient PermissionError to readers
    during the swap (CI finding — third member of the same sharing
    family, after the write and lock paths). POSIX never retries."""
    for attempt in range(200):
        try:
            return Path(path).read_text("utf-8")
        except PermissionError:
            if os.name != "nt" or attempt == 199:
                raise
            time.sleep(0.05)


def _atomic_write(path, data, boundary=None):
    """Atomic, symlink-safe, durable write: O_NOFOLLOW + fsync + os.replace.

    O_NOFOLLOW + the is_symlink check block TOCTOU symlink attacks on the
    target itself; when `boundary` is given, parent directories up to it are
    also checked so a symlinked parent dir cannot redirect the write outside
    the trust boundary. os.replace guarantees readers see the old or the new
    file, never a torn one; fsync before the rename makes the new content
    survive power loss (without it the rename can land while the data is
    still in page cache)."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"refusing to write through a symlink: {path}")
    if boundary is not None:
        _reject_symlinked_parents(path, boundary)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | nofollow | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data.encode("utf-8") if isinstance(data, str) else data)
        os.fsync(fd)
    finally:
        os.close(fd)
    # Windows: os.replace raises PermissionError while another process
    # momentarily holds the destination open (Python's open() does not
    # grant FILE_SHARE_DELETE). Readers are short-lived — retry briefly
    # (CI finding: 1/12 parallel writers lost this race on
    # windows-latest; POSIX never enters the loop).
    for attempt in range(200):
        try:
            os.replace(tmp, str(path))
            break
        except PermissionError:
            if os.name != "nt" or attempt == 199:
                raise
            time.sleep(0.05)
    # full durability: fsync the DIRECTORY too, or the rename itself can
    # be lost on power failure (auditor finding — the docs promised more
    # than fsync-the-file delivers). Windows has no dir-fsync; skip.
    if os.name != "nt":
        try:
            dfd = os.open(os.path.dirname(os.path.abspath(str(path))) or ".",
                          os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass


# ────────────────────────────────────────────────────────────────
# Bilingual tokenization + light stemming (English + Arabic)
# ────────────────────────────────────────────────────────────────
_WORD_RUN = re.compile(r"[\w؀-ۿ]+", re.UNICODE)
# Scripts written WITHOUT spaces (CJK ideographs, kana, Hangul, Thai): a
# "word run" there is a whole phrase, and most words are 1-2 characters —
# both break whole-word indexing (measured: Chinese/Japanese recall@1 was
# 3/6 before this). Runs in these ranges are indexed as character BIGRAMS
# instead — the standard search-engine technique. Space-separated scripts
# (Latin, Cyrillic, Arabic, Greek, ...) keep whole words >= 3 chars.
_NOSPACE_RE = re.compile("[%s]" % (
    "⺀-鿿"      # CJK radicals + unified ideographs
    "㐀-䶿"      # CJK extension A
    "豈-﫿"      # CJK compatibility ideographs
    "぀-ヿ"      # hiragana + katakana
    "가-힯"      # hangul syllables
    "฀-๿"))    # thai


def _bigrams(chars):
    if len(chars) < 2:
        return ["".join(chars)]
    return ["".join(chars[i:i + 2]) for i in range(len(chars) - 1)]


def _tokenize(text):
    """Script-aware tokenizer shared by every indexing path (keys,
    co-occurrence, embeddings, destructive-op gates)."""
    out = []
    for run in _WORD_RUN.findall(text or ""):
        alpha, nospace = [], []
        for ch in run:
            if _NOSPACE_RE.match(ch):
                if alpha:
                    if len(alpha) >= 3:
                        out.append("".join(alpha))
                    alpha = []
                nospace.append(ch)
            else:
                if nospace:
                    out.extend(_bigrams(nospace))
                    nospace = []
                alpha.append(ch)
        if len(alpha) >= 3:
            out.append("".join(alpha))
        if nospace:
            out.extend(_bigrams(nospace))
    return out

STOPWORDS = frozenset({
    # English
    "the", "and", "for", "that", "with", "from", "this", "these", "those",
    "have", "has", "are", "was", "were", "not", "but", "you", "all", "can",
    "her", "him", "his", "she", "they", "them", "our", "out", "use", "using",
    "used", "what", "when", "where", "which", "who", "why", "how",
    "is", "be", "been", "being", "does", "did", "will", "its", "it",
    "my", "our", "your", "their",
    # Arabic
    "من", "على", "في", "الى", "إلى", "التي", "التى", "الذي", "الذى", "هذا",
    "هذه", "عند", "قد", "ماذا", "اي", "أي", "لماذا", "كيف", "ما", "عن", "مع",
    "او", "أو", "ثم", "لكن", "بعد", "قبل", "كل", "بعض", "نحن", "انت", "أنت",
    "هو", "هي", "هم", "كان", "يكون", "ان", "أن", "إن", "لا", "لم", "لن",
    "لقد", "ذالك", "ذلك", "هناك",
})

# Cross-language normalization seed: maps common transliterated Arabic tech
# terms to their English equivalents so both spellings hit the same node.
NORMALIZE = {
    "بايثون": "python", "بايثوني": "python",
    "ريأكت": "react", "رياكت": "react",
    "قاعدة": "database", "بيانات": "database", "قواعد": "database",
    "واجهة": "frontend", "واجهات": "frontend",
    "خلفية": "backend",
    "تايبسكريبت": "typescript", "تايب سكريبت": "typescript",
    "نود": "node", "فلاسك": "flask", "ريلز": "rails",
    "إلكترون": "electron",
    "كودكس": "codex", "جيمناي": "gemini",
}
# Multi-word entries must be replaced BEFORE tokenization (the tokenizer
# splits on spaces, so a dict lookup per token can never match them).
_NORMALIZE_PHRASES = {k: v for k, v in NORMALIZE.items() if " " in k}

# High-precision concept seed: tool → category, so a question asked by
# CATEGORY ("what css framework?") finds a memory that only names the TOOL
# ("tailwind"). Applied to memories AND queries, so either side naming the
# tool matches the other side naming the category. One-directional on
# purpose (specific → general): the reverse would explode a general query
# into every tool name. Category keys land on many nodes, so IDF keeps
# them from ever outranking an exact term match. Only unambiguous tech
# terms belong here — polysemous words (black, express, spring, phoenix,
# prettier, oracle) are deliberately excluded: a false category on an
# everyday sentence is worse than a missed synonym.
CONCEPT_SEED = {
    # css / styling
    "tailwind": ("css", "styling"), "bootstrap": ("css", "styling"),
    "bulma": ("css", "styling"), "sass": ("css", "styling"),
    "scss": ("css", "styling"),
    # frontend
    "react": ("frontend", "javascript"), "vue": ("frontend", "javascript"),
    "svelte": ("frontend", "javascript"),
    "angular": ("frontend", "javascript"),
    "nextjs": ("frontend", "javascript"), "nuxt": ("frontend", "javascript"),
    # backend
    "django": ("backend", "python"), "flask": ("backend", "python"),
    "fastapi": ("backend", "python"), "rails": ("backend", "ruby"),
    "laravel": ("backend", "php"),
    # databases / storage
    "postgres": ("database",), "postgresql": ("database",),
    "mysql": ("database",), "sqlite": ("database",),
    "mariadb": ("database",), "mongodb": ("database",),
    "redis": ("database", "cache"), "memcached": ("cache",),
    "dynamodb": ("database",), "cassandra": ("database",),
    "elasticsearch": ("database", "search"),
    # orm
    "prisma": ("orm", "database"), "sqlalchemy": ("orm", "database"),
    "sequelize": ("orm", "database"),
    # cloud / hosting / cdn
    "aws": ("cloud", "hosting"), "azure": ("cloud", "hosting"),
    "gcp": ("cloud", "hosting"), "hetzner": ("cloud", "hosting"),
    "digitalocean": ("cloud", "hosting"), "linode": ("cloud", "hosting"),
    "vercel": ("hosting", "deployment"), "netlify": ("hosting", "deployment"),
    "heroku": ("hosting", "deployment"), "cloudflare": ("cdn", "dns"),
    # containers / devops / ci
    "docker": ("container", "devops"), "kubernetes": ("container", "devops"),
    "terraform": ("devops", "infrastructure"), "ansible": ("devops",),
    "jenkins": ("devops", "ci"), "circleci": ("ci",),
    # testing
    "pytest": ("testing",), "jest": ("testing",), "mocha": ("testing",),
    "cypress": ("testing",), "playwright": ("testing",),
    "selenium": ("testing",),
    # payments
    "stripe": ("payment", "billing"), "paypal": ("payment", "billing"),
    # monitoring / logs / errors
    "sentry": ("errors", "monitoring"), "datadog": ("monitoring",),
    "grafana": ("monitoring", "dashboard"),
    "prometheus": ("monitoring", "metrics"),
    "loki": ("logs", "monitoring"), "kibana": ("logs", "dashboard"),
    # queues / messaging
    "kafka": ("queue", "messaging"), "rabbitmq": ("queue", "messaging"),
    "celery": ("queue", "tasks"), "sqs": ("queue", "messaging"),
    # auth
    "oauth": ("auth", "authentication"), "jwt": ("auth", "authentication"),
    "sso": ("auth", "authentication"), "bearer": ("auth", "authentication"),
    # mobile
    "flutter": ("mobile",), "android": ("mobile",), "ios": ("mobile",),
    # lint / support / analytics / cms / vcs
    "eslint": ("lint",), "ruff": ("lint",), "flake8": ("lint",),
    "intercom": ("support",), "zendesk": ("support",),
    "mixpanel": ("analytics",), "amplitude": ("analytics",),
    "wordpress": ("cms",), "drupal": ("cms",),
    "github": ("git",), "gitlab": ("git",), "bitbucket": ("git",),
}

# Arabic identity pronouns: queries like "what is my name" / "من أنا" carry
# no content keys after stopword removal, so we fall back to identity keys.
PRONOUN_FALLBACK = {
    "انا", "أنا", "انني", "أني", "اسمي", "اسمنا", "مدينتي", "مدينتنا",
    "مشروعي", "مشروعنا", "عملي", "اعمل", "أعمل", "اين", "أين", "ماذا",
    "مشروعه", "تعمل", "تعملون", "تعملين",
    "name", "myself", "who", "whoami", "mine",
}
IDENTITY_KEYS = {"user", "project", "city", "name", "المستخدم", "المشروع",
                 "المدينة", "الاسم"}
# Storage-side identity trigger (6.1.0): a STORED fact earns identity keys
# only for explicit first-person identity statements — a fact that merely
# CONTAINS "name" or "اعمل" must not (auditor finding: "file name must
# match the class name" outranked the user's actual name on identity
# queries, and an Arabic distractor with a bare pronoun beat an English
# name fact). Queries keep the broad PRONOUN_FALLBACK behavior.
_IDENT_POSSESSIVE = {"اسمي", "اسمنا", "مدينتي", "مدينتنا", "مشروعي",
                     "مشروعنا", "عملي", "myself"}
_IDENT_FIRST_PERSON = {"my", "our", "انا", "أنا", "نحن", "اني", "أني",
                       "انني", "إني"}
_IDENT_NOUNS = {"name", "project", "city", "team", "company", "اسم",
                "الاسم", "مشروع", "المشروع", "مدينة", "المدينة"}
# Facet map (6.2.1): an identity noun grants only ITS OWN identity keys —
# granting the whole IDENTITY_KEYS set gave "my city is Riyadh" the `name`
# and `user` keys too, so it outranked the user's actual name on
# "what is my name" (auditor finding, reproduced). Personal facets (name,
# team, company) also carry the user keys; place facets don't.
_NAME_FACET = ("name", "الاسم", "user", "المستخدم")
_CITY_FACET = ("city", "المدينة")
_PROJ_FACET = ("project", "المشروع")
_USER_FACET = ("user", "المستخدم")
_IDENT_FACETS = {
    "name": _NAME_FACET, "اسم": _NAME_FACET, "الاسم": _NAME_FACET,
    "اسمي": _NAME_FACET, "اسمنا": _NAME_FACET,
    "city": _CITY_FACET, "مدينة": _CITY_FACET, "المدينة": _CITY_FACET,
    "مدينتي": _CITY_FACET, "مدينتنا": _CITY_FACET,
    "project": _PROJ_FACET, "مشروع": _PROJ_FACET, "المشروع": _PROJ_FACET,
    "مشروعي": _PROJ_FACET, "مشروعنا": _PROJ_FACET,
    "team": _USER_FACET, "company": _USER_FACET,
    "عملي": _USER_FACET, "myself": _USER_FACET,
}


def _facet_keys(tokens):
    """Identity keys for exactly the facets the text names (deterministic
    order). Empty when no mapped identity noun is present."""
    out = []
    for t in sorted(tokens):
        for k in _IDENT_FACETS.get(t, ()):
            if k not in out:
                out.append(k)
    return out

_AR_SUFFIXES = ("تها", "تهن", "تنا", "تهم", "ية", "ون", "ين", "ان",
                "ات", "ها", "هن", "هم", "نا", "ة", "ي", "ت", "ن")
_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "و", "ف", "ب", "ل", "ك", "س")
# Arabic broken plurals cannot be stemmed by suffix stripping; a small seed
# dictionary unifies singular + broken plural onto one canonical stem.
_BROKEN_PLURALS = {
    "قاعدة": "قاعد", "مدينة": "مدين", "دولة": "دول", "أداة": "أدا",
    "مشروع": "مشروع", "ملف": "ملف", "وكيل": "وكيل", "خبير": "خبير",
    "قرار": "قرار", "رابط": "رابط", "بيان": "بيان", "حرف": "حرف",
    "كلمة": "كلم", "عقدة": "عقد", "نموذج": "نموذج",
    "قواعد": "قاعد", "مدن": "مدين", "دول": "دول", "أدوات": "أدا",
    "مشاريع": "مشروع", "ملفات": "ملف", "وكلاء": "وكيل", "خبراء": "خبير",
    "قرارات": "قرار", "روابط": "رابط", "بيانات": "بيان", "حروف": "حرف",
    "كلمات": "كلم", "عقد": "عقد", "نماذج": "نموذج",
    "وظيفة": "وظيف", "وظائف": "وظيف", "رسالة": "رسال", "رسائل": "رسال",
    "جدول": "جدول", "جداول": "جدول",
}


def stem(w):
    """Light bilingual stemmer. Arabic: prefix/suffix stripping + broken-plural
    seed dictionary. English: common suffix stripping."""
    if w and "؀" <= w[0] <= "ۿ":  # Arabic
        # full-word broken-plural lookup FIRST: stripping a "prefix" that
        # is actually the first ROOT letter (كلمة -> لمة) used to bypass
        # the dictionary entirely (auditor finding)
        if w in _BROKEN_PLURALS:
            return _BROKEN_PLURALS[w]
        s = w
        for p in _AR_PREFIXES:
            if s.startswith(p) and len(s) - len(p) >= 3:
                stripped = s[len(p):]
                if stripped in _BROKEN_PLURALS:
                    return _BROKEN_PLURALS[stripped]
                s = stripped
                break
        if s in _BROKEN_PLURALS:
            return _BROKEN_PLURALS[s]
        for suf in _AR_SUFFIXES:
            if s.endswith(suf) and len(s) - len(suf) >= 3:
                s = s[:-len(suf)]
                break
        return s or w
    for suf in ("ing", "ied", "ies", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            if suf in ("ies", "ied"):
                return w[:-3] + "y"
            if suf == "es":
                if w.endswith(("ases", "eses", "ises")):
                    return w[:-1]          # databases -> database
                if w[-3] in "sxz" and len(w) > 4:
                    return w[:-2]          # boxes -> box
            return w[:-len(suf)]
    return w


# ────────────────────────────────────────────────────────────────
# RelatedTerms — automatic co-occurrence index (replaces any manual
# synonym dictionary). Two terms that appear together in many nodes
# are related; a constrained 2-hop PageRank bridges sparse corpora.
# ────────────────────────────────────────────────────────────────
class RelatedTerms:
    """Build cost O(D * k^2); query cost < 5 ms. Works for EN + AR."""

    def __init__(self, corpus, max_terms_per_doc=12, min_df=1):
        self.max_terms_per_doc = max_terms_per_doc
        self.df = Counter()
        self.cooc = defaultdict(Counter)
        self._build(list(corpus), min_df)

    def _tokens(self, text):
        seen, out = set(), []
        for raw in _tokenize((text or "").lower()):
            if raw in STOPWORDS:
                continue
            t = stem(raw)
            # no-space-script bigrams are 1-2 chars by construction —
            # only alphabetic tokens carry the 3-char floor
            if (len(t) < 3 and not _NOSPACE_RE.match(t)) \
                    or t in STOPWORDS or t in seen:
                continue
            seen.add(t)
            out.append(t)
            if len(out) >= self.max_terms_per_doc:
                break
        return out

    def _build(self, corpus, min_df):
        docs = []
        for text in corpus:
            terms = self._tokens(text)
            if not terms:
                continue
            docs.append(terms)
            for t in set(terms):
                self.df[t] += 1
        keep = {t for t, c in self.df.items() if c >= min_df}
        for terms in docs:
            uniq = [t for t in dict.fromkeys(terms) if t in keep]
            n = len(uniq)
            for i in range(n):
                row = self.cooc[uniq[i]]
                for j in range(n):
                    if i != j:
                        row[uniq[j]] += 1

    @staticmethod
    def _score(co, dfa, dfb):
        """Ochiai coefficient (cosine for binary co-occurrence), in [0, 1]."""
        return co / math.sqrt(dfa * dfb) if dfa and dfb else 0.0

    def related(self, word, top_k=5, max_hops=2, damping=0.55):
        """Constrained PageRank over the co-occurrence graph.

        Hop 1 is direct Ochiai co-occurrence; hops 2..max spread similarity
        through shared neighbours, so A~C is found even when A and C never
        co-occur directly. On dense corpora hop 1 dominates."""
        if not word:
            return []
        t = stem(word.lower().strip())
        if t not in self.cooc or not self.df.get(t):
            return self._fuzzy(t, top_k)

        def row(term):
            dfa = self.df[term]
            out = {}
            for nb, co in self.cooc[term].items():
                if nb == term:
                    continue
                dfb = self.df.get(nb, 0)
                if dfb:
                    out[nb] = self._score(co, dfa, dfb)
            s = sum(out.values())
            return {nb: v / s for nb, v in out.items()} if s > 0 else {}

        scores = defaultdict(float)
        wave = row(t)
        for nb, p in wave.items():
            scores[nb] += p
        for hop in range(2, max_hops + 1):
            nxt = defaultdict(float)
            for node, mass in wave.items():
                decay = damping ** (hop - 1)
                for nb, p in row(node).items():
                    if nb == t:
                        continue
                    nxt[nb] += mass * p * decay
            if not nxt:
                break
            for nb, m in nxt.items():
                scores[nb] += m
            wave = nxt

        if not scores:
            return self._fuzzy(t, top_k)
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        mx = ranked[0][1]
        return [(nb, round(s / mx, 4)) for nb, s in ranked[:top_k]]

    def _fuzzy(self, t, top_k):
        """Edit-distance fallback for unknown words (typos, inflections).
        This is the pattern-completion entry point for unseen queries."""
        best, tl = [], len(t)
        for w in self.df:
            if abs(len(w) - tl) > 2:
                continue
            r = self._ratio(t, w)
            if r >= 0.6:
                best.append((w, r))
        best.sort(key=lambda x: (-x[1], x[0]))
        return [(w, round(r, 4)) for w, r in best[:top_k]]

    @staticmethod
    def _ratio(a, b):
        if a == b:
            return 1.0
        la, lb = len(a), len(b)
        if not la or not lb:
            return 0.0
        prev = list(range(lb + 1))
        for i, ca in enumerate(a, 1):
            cur = [i] + [0] * lb
            for j, cb in enumerate(b, 1):
                cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                             prev[j - 1] + (ca != cb))
            prev = cur
        return 1.0 - prev[lb] / max(la, lb)


# ────────────────────────────────────────────────────────────────
# HashEmbed — offline lexical embeddings (signed n-gram hashing).
# No network, no keys, no model download. Catches word/char overlap
# and morphological variants; used for re-ranking and dream clustering.
# ────────────────────────────────────────────────────────────────
class HashEmbed:
    def __init__(self, dim=512):
        self.dim = dim
        self._cache = {}

    def embed(self, text):
        if text in self._cache:
            return self._cache[text]
        v = [0.0] * self.dim
        toks = [t for t in _tokenize((text or "").lower())
                if t not in STOPWORDS]
        toks = [stem(t) for t in toks]
        feats = []
        for n in (1, 2):
            for i in range(len(toks) - n + 1):
                feats.append("w%d:%s" % (n, " ".join(toks[i:i + n])))
        for tok in toks:
            pad = "#" + tok + "#"
            for n in (3, 4, 5):
                for i in range(len(pad) - n + 1):
                    feats.append("c%d:%s" % (n, pad[i:i + n]))
        from hashlib import blake2b
        for f in feats:
            h = blake2b(f.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % self.dim
            v[idx] += 1.0 if (h[4] & 1) == 0 else -1.0
        if len(self._cache) < 4096:
            self._cache[text] = v
        return v

    def similarity(self, a, b):
        va, vb = self.embed(a), self.embed(b)
        dot = sum(x * y for x, y in zip(va, vb))
        na = math.sqrt(sum(x * x for x in va)) or 1.0
        nb = math.sqrt(sum(y * y for y in vb)) or 1.0
        return max(0.0, dot / (na * nb))


# ────────────────────────────────────────────────────────────────
# Layer 2: Hippocampus — the weighted concept graph
# ────────────────────────────────────────────────────────────────
class Hippocampus:
    """Light graph: nodes (memories) + weighted edges (relations).
    Recall = local spreading activation (<= RECALL_RADIUS hops), fused
    with direct keyword matches via Reciprocal Rank Fusion + IDF."""

    def __init__(self, path):
        self.path = path
        self.nodes = {}   # id -> {text, weight, peak_weight, last_accessed,
        #                          access_count, created, confidence, keys, history}
        self.edges = {}   # id -> {neighbor_id: {relation, weight}}
        self.meta = {}    # small persisted strings (e.g. last_edge_decay)
        self.related = None
        self.embedder = HashEmbed()
        self._deleted = set()   # node ids deleted this session (see _save merge)
        self._decayed = {}      # nid -> decayed weight this session: decay
        #   touches ONLY salience, so it must never whole-copy a node over
        #   a concurrent confirm's fresh counters (auditor finding, wave 2:
        #   confirm racing dream lost the increment in 20/25 trials)
        self._bumped = {}       # nid -> reinforcement delta this session:
        #   applied ON TOP of the fresh disk copy inside the locked merge,
        #   so two processes confirming the same fact both count (auditor
        #   finding: read-modify-write raced — 20 parallel confirms landed
        #   as 8-14)
        self._dirty = set()     # node ids THIS session actually modified:
        #   the merge overwrites the disk only with these — untouched
        #   stale copies used to clobber another process's confirmations
        #   (auditor finding: bump in process A, unrelated remember in
        #   process B → A's access_count silently reset)
        self._pruned_edges = set()  # (a, b) edge pairs pruned this session:
        #   without this, an edge decayed to empty and removed from
        #   self.edges is silently REVIVED from disk by the read-merge-write
        #   (edge deletions, unlike node deletions, weren't tracked)
        self._load()

    # -- persistence -------------------------------------------------
    def _quarantine(self, reason):
        """Never silently erase a user's memory: quarantine and start fresh."""
        bak = self.path.with_suffix(
            ".json.corrupt-%s-%d" % (_now().strftime("%H%M%S%f"),
                                     os.getpid()))
        try:
            self.path.rename(bak)
            print("warning: could not read %s (%s).\n"
                  "  corrupt copy saved as %s; starting with empty memory."
                  % (self.path.name, reason, bak.name), file=sys.stderr)
        except OSError:
            pass
        return {}

    @staticmethod
    def _finite(value, default, lo=None, hi=None):
        """float() with repair: non-numeric AND non-finite (NaN/Infinity)
        values become the default, optionally clamped (fuzzer finding: a
        NaN weight poisons every comparison and ranking downstream, and
        float() alone happily accepts it)."""
        try:
            f = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(f):
            return default
        if lo is not None:
            f = max(lo, f)
        if hi is not None:
            f = min(hi, f)
        return f

    def _repair_nodes(self, raw):
        """Repair a nodes dict fresh off the disk — shared by _load AND the
        _save read-merge-write. The merge used to import raw disk nodes
        past all of _load's repair, so a corrupt file left by a hand-edit
        or another (buggier) process re-poisoned a healthy session's graph
        on its next save (fuzzer finding)."""
        out = {}
        for nid, n in raw.items():
            if not isinstance(n, dict) or not isinstance(n.get("text", ""), str):
                continue
            n.setdefault("text", "")
            n.setdefault("weight", 1.0)
            n.setdefault("peak_weight", n.get("weight", 1.0))
            n.setdefault("confidence", 1.0)
            n.setdefault("access_count", 0)
            n.setdefault("keys", [])
            n.setdefault("last_accessed", _now().isoformat())
            n.setdefault("created", _now().isoformat())
            # timestamps must be ISO strings: a hand-edit that leaves a
            # number here would crash decay with TypeError, not ValueError
            # (auditor finding) — repair to "now" like the numeric fields
            for f in ("last_accessed", "created"):
                if not isinstance(n.get(f), str):
                    n[f] = _now().isoformat()
            # numeric fields: repair non-numeric AND non-finite values, and
            # clamp to the range every write path maintains (auditor +
            # fuzzer findings)
            n["weight"] = self._finite(n["weight"], 1.0, 0.0, 1.0)
            n["peak_weight"] = self._finite(n["peak_weight"], 1.0, 0.0, 1.0)
            n["confidence"] = self._finite(n["confidence"], 1.0, 0.0, 1.0)
            n["access_count"] = int(self._finite(n["access_count"], 0, 0))
            # history must be a list — correct() appends to it (fuzzer
            # finding: a scalar history crashed reconsolidation)
            if not isinstance(n.get("history", []), list):
                n["history"] = []
            # provenance + validity (6.0.0): older graphs get honest
            # defaults — origin "unknown", validity open since creation
            if not isinstance(n.get("origin"), dict):
                n["origin"] = {"by": "unknown", "session": None,
                               "via": "unknown"}
            if not isinstance(n.get("valid_from"), str):
                n["valid_from"] = n["created"]
            if not isinstance(n.get("valid_to"), str):
                n["valid_to"] = None
            # lexicographic comparison assumes ISO format — repair any
            # hand-edited non-ISO value instead of comparing garbage
            for f, d in (("valid_from", n["created"]), ("valid_to", None)):
                v = n.get(f)
                if isinstance(v, str):
                    try:
                        datetime.fromisoformat(v)
                    except ValueError:
                        n[f] = d
            if not (n.get("superseded_by") is None or
                    isinstance(n.get("superseded_by"), str)):
                n.pop("superseded_by", None)
            # keys must be a list of strings; a bare string would iterate
            # character-by-character and a non-string element would crash
            # the re.sub below (auditor finding)
            raw_keys = n.get("keys", [])
            if not isinstance(raw_keys, list):
                raw_keys = []
            n["keys"] = [re.sub(r'[،؛؟!."\']', '', k).strip()[:100]
                         for k in raw_keys if isinstance(k, str)]
            n["keys"] = [k for k in n["keys"] if k][:24]
            # the write-path text cap must hold on the LOAD path too — a
            # synced/hand-edited graph with one 100MB node used to defeat
            # it entirely (auditor finding)
            if len(n["text"]) > MAX_TEXT_CHARS:
                n["text"] = n["text"][:MAX_TEXT_CHARS]
            out[nid] = n
        return out

    def _repair_edges(self, raw, nodes):
        """Same contract for edges: only dict entries between existing
        nodes survive, with a finite clamped weight and a string relation
        (fuzzer finding: a null/list edge weight crashed the dream's edge
        decay; orphan edges crashed recall with KeyError)."""
        out = {}
        for nid, nbrs in raw.items():
            if nid not in nodes or not isinstance(nbrs, dict):
                continue
            clean = {}
            for nbr, e in nbrs.items():
                if nbr not in nodes or not isinstance(e, dict):
                    continue
                e["weight"] = self._finite(e.get("weight", 1.0), 1.0, 0.0, 1.0)
                if not isinstance(e.get("relation", "related"), str):
                    e["relation"] = "related"
                clean[nbr] = e
            if clean:
                out[nid] = clean
        return out

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(_read_text_retry(self.path))
            if not isinstance(data, dict):
                raise ValueError("graph.json is not a JSON object")
            if not isinstance(data.get("nodes", {}), dict) or \
                    not isinstance(data.get("edges", {}), dict):
                raise ValueError("nodes/edges have the wrong structure")
        except (json.JSONDecodeError, ValueError) as e:
            data = self._quarantine(e)
        self.nodes = self._repair_nodes(data.get("nodes", {}))
        self.edges = self._repair_edges(data.get("edges", {}), self.nodes)
        meta = data.get("meta", {})
        # whitelisted keys only: a hand-edited graph could otherwise grow
        # meta without bound, one 64-char value per arbitrary key
        # (auditor finding, 6.2.5)
        self.meta = ({k: v[:64] for k, v in meta.items()
                      if k in _META_KEYS and isinstance(v, str)
                      and v.isprintable()}
                     if isinstance(meta, dict) else {})
        # a FUTURE-dated decay marker (clock skew, hand edit, synced graph)
        # would freeze edge homeostasis forever under max-wins merging —
        # clamp it to today so decay resumes tomorrow (auditor finding,
        # 6.2.3: marker "2099-01-01" disabled synaptic pruning permanently)
        today = str(_now().date())
        if self.meta.get("last_edge_decay", "") > today:
            self.meta["last_edge_decay"] = today

    def _save(self):
        """Locked read-merge-write: concurrent agent processes cannot lose
        each other's writes. Inside the lock we re-read the graph from disk
        and merge nodes/edges written by other processes since our load
        (our changes win per node; our deletions stay deleted)."""
        lock_path = self.path.with_suffix(".json.lock")
        # open the lock with O_NOFOLLOW: a symlinked lock file must never
        # truncate its target (auditor finding — plain open(..., "w") did).
        # And check the PARENT chain before creating it: through a symlinked
        # .mind root the lock file itself used to be created outside the
        # trust boundary — the one write that escaped (test-suite finding)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        _reject_symlinked_parents(lock_path, self.path.parent)
        try:
            lock_fd = os.open(str(lock_path),
                              os.O_WRONLY | os.O_CREAT | nofollow, 0o644)
        except OSError as e:
            raise ValueError("refusing unsafe lock file %s: %s" % (lock_path, e))
        with os.fdopen(lock_fd, "w") as lockf:
            lock_backend = None
            try:
                import fcntl
                fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
                lock_backend = ("fcntl", fcntl)
            except ImportError:
                try:
                    import msvcrt
                except ImportError:            # neither fcntl nor msvcrt:
                    lock_backend = None        # degrade to atomic-write-only
                else:
                    lockf.seek(0)
                    # LK_LOCK is not an indefinite blocking lock like flock:
                    # the CRT retries once per second, 10 times, then raises
                    # OSError — so a save contended for >10s would crash the
                    # very scenario the lock exists for. Keep waiting instead,
                    # exactly like the POSIX path.
                    for _attempt in range(18):   # up to ~3 min: LK_LOCK waits
                        try:                        # ~10s per call
                            msvcrt.locking(lockf.fileno(), msvcrt.LK_LOCK, 1)
                            break
                        except OSError:
                            continue                # keep waiting, bounded
                    else:
                        # a hung (alive but frozen) holder must not
                        # livelock us forever (auditor finding)
                        raise ValueError("could not acquire the graph lock "
                                         "after ~3 minutes — is another "
                                         "process hung?")
                    lock_backend = ("msvcrt", msvcrt)
            try:
                if self.path.exists():
                    try:
                        disk = json.loads(_read_text_retry(self.path))
                    except (json.JSONDecodeError, ValueError) as e:
                        # never silently overwrite a corrupt graph during a
                        # live save: quarantine it exactly like _load does
                        # (auditor finding — the README promise held only
                        # on the load path)
                        self._quarantine(e)
                        disk = {}
                    dn = disk.get("nodes", {}) if isinstance(disk, dict) else {}
                    de = disk.get("edges", {}) if isinstance(disk, dict) else {}
                    if isinstance(dn, dict) and isinstance(de, dict):
                        # repair the disk copy BEFORE merging: without this
                        # the merge imported raw disk content past all of
                        # _load's validation, so one corrupt file poisoned
                        # a healthy session's next save (fuzzer finding)
                        merged_n = {k: v
                                    for k, v in self._repair_nodes(dn).items()
                                    if k not in self._deleted}
                        # field-freshness: only nodes this session touched
                        # overwrite the disk copy; for everything else the
                        # DISK is fresher (auditor finding)
                        for k in self._dirty:
                            if k in self.nodes:
                                merged_n[k] = self.nodes[k]
                        for k, v in self.nodes.items():
                            merged_n.setdefault(k, v)
                        # reinforcement deltas: re-apply OUR confirms on
                        # top of the freshest copy, so concurrent confirms
                        # from other processes are added to, never raced
                        now_iso = _now().isoformat()
                        for k, d in self._bumped.items():
                            n = merged_n.get(k)
                            if n is None or k in self._dirty:
                                continue   # dirty copy already includes it
                            n["access_count"] = int(self._finite(
                                n.get("access_count", 0), 0, 0)) + d
                            n["weight"] = min(1.0, self._finite(
                                n.get("weight", 1.0), 1.0, 0.0, 1.0)
                                + BOOST_PER_ACCESS * d)
                            n["peak_weight"] = max(
                                self._finite(n.get("peak_weight", 1.0),
                                             1.0, 0.0, 1.0), n["weight"])
                            n["last_accessed"] = now_iso
                        # decay updates: salience only, on the fresh copy —
                        # min() because decay never raises a weight, and a
                        # concurrent confirm's boost must win the tie
                        for k, w in self._decayed.items():
                            n = merged_n.get(k)
                            if n is None or k in self._dirty \
                                    or k in self._bumped:
                                continue
                            n["weight"] = min(self._finite(
                                n.get("weight", 1.0), 1.0, 0.0, 1.0), w)
                        # Build the merged edges from the DISK copy, stripping
                        # both deleted nodes and edges this process pruned,
                        # THEN apply our live edges. Order matters: pruned
                        # pairs must be removed from the disk copy *before*
                        # update, so a live edge we legitimately (re)created
                        # this session still wins — filtering after update
                        # would wrongly clobber it (auditor finding).
                        merged_e = {}
                        for k, v in self._repair_edges(de, merged_n).items():
                            if k in self._deleted:
                                continue
                            merged_e[k] = {nbr: e for nbr, e in v.items()
                                           if nbr not in self._deleted
                                           and (k, nbr) not in self._pruned_edges}
                        merged_e.update(self.edges)
                        # drop node-keys the disk left empty after stripping
                        merged_e = {k: v for k, v in merged_e.items() if v}
                        self.nodes, self.edges = merged_n, merged_e
                    # meta merge (max-wins: ISO dates compare) — inside
                    # the same disk-copy scope as nodes/edges
                    disk_meta = (disk.get("meta", {})
                                 if isinstance(disk, dict) and
                                 isinstance(disk.get("meta", {}), dict)
                                 else {})
                    merge_today = str(_now().date())
                    for mk, mv in disk_meta.items():
                        if mk not in _META_KEYS or not (
                                isinstance(mv, str) and mv.isprintable()):
                            continue
                        mv = mv[:64]
                        if mk == "last_edge_decay" and mv > merge_today:
                            mv = merge_today    # future marker = skew garbage
                        if mv > self.meta.get(mk, ""):
                            self.meta[mk] = mv
                _atomic_write(self.path, json.dumps(
                    {"nodes": self.nodes, "edges": self.edges,
                     "meta": self.meta},
                    ensure_ascii=False, indent=2),
                    boundary=self.path.parent)
                # deletions/prunes are now persisted to disk; clear them so
                # they can't poison a later _save in the same process (auditor
                # finding: a stale _pruned_edges entry deleted a live edge a
                # subsequent op had recreated)
                self._deleted.clear()
                self._pruned_edges.clear()
                self._dirty.clear()
                self._bumped.clear()
                self._decayed.clear()
            finally:
                if lock_backend is not None:
                    name, module = lock_backend
                    if name == "fcntl":
                        module.flock(lockf.fileno(), module.LOCK_UN)
                    elif name == "msvcrt":
                        lockf.seek(0)
                        module.locking(lockf.fileno(), module.LK_UNLCK, 1)

    @staticmethod
    def _id(text):
        # content addressing only — no security property is derived from
        # the hash; md5[:12] keeps existing graphs' node ids stable
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

    # -- key extraction ----------------------------------------------
    def _ensure_related(self):
        if self.related is None:
            corpus = [n.get("text", "") for n in self.nodes.values()]
            if corpus:
                self.related = RelatedTerms(corpus, min_df=1)

    def _extract_keys(self, text, is_query=False):
        """Four cooperating layers:
        1. NORMALIZE seed (cross-language term bridging, incl. multi-word
           phrases handled before tokenization — the tokenizer would split
           them otherwise and the mapping would never match)
        2. CONCEPT_SEED (tool → category, both directions meet on the
           category key — closes the cross-domain synonymy gap for the
           curated tech vocabulary)
        3. co-occurrence expansion (RelatedTerms, self-building)
        4. identity keys — added to queries with no content keys, and to
           any text (query or memory) that mentions identity pronouns, so
           "my name is X" and "what is my name" land on the same keys.
           Content-free stored memories get NO identity fallback."""
        cleaned = re.sub(r'[،؛؟!.,"\']', ' ', text)
        for phrase, rep in _NORMALIZE_PHRASES.items():
            if phrase in cleaned:
                cleaned = cleaned.replace(phrase, " %s " % rep)
        words = _tokenize(cleaned.lower())
        # insertion-ordered dict, not a set: the [:24] truncation below must
        # be deterministic. Set iteration order varies with str-hash
        # randomization, so the same text could store a different key subset
        # on every machine/run — breaking the "same input, same graph"
        # property the dream cycle's determinism rests on (auditor finding).
        keys = {}
        for w in words:
            if w in STOPWORDS:
                continue
            t = NORMALIZE.get(w, w)
            keys.setdefault(t)
            # concept seed: a memory naming the tool also earns the
            # category keys, and vice versa on the query side, so
            # "what css framework" reaches the tailwind memory
            for cat in CONCEPT_SEED.get(t, ()):
                keys.setdefault(cat)
        text_tokens = set(re.findall(r'[\w؀-ۿ]+', cleaned.lower()))
        # zero-key black hole (auditor finding): text made entirely of
        # short tokens ("db ai os") used to be stored unreachable — fall
        # back to indexing the short tokens themselves. Remember whether
        # any REAL content keys existed first: the short-token fallback
        # must not disarm the identity fallback for queries like
        # "who am I" (auditor finding, wave 2)
        had_content = bool(keys)
        if not keys:
            for t in sorted(text_tokens):
                if len(t) >= 2 and t not in STOPWORDS:
                    keys.setdefault(t)
        if is_query:
            if text_tokens & PRONOUN_FALLBACK or not had_content:
                # a query that NAMES a facet ("what is my name") gets only
                # that facet's keys, so place/project identity facts can't
                # compete; a facet-less query ("who am I") legitimately
                # wants every identity fact and keeps the full set
                facets = _facet_keys(text_tokens)
                for k in (facets if (facets and had_content)
                          else sorted(IDENTITY_KEYS)):
                    keys.setdefault(k)
        elif (text_tokens & _IDENT_POSSESSIVE) or \
                ((text_tokens & _IDENT_FIRST_PERSON
                  or text_tokens & {"user", "المستخدم"})
                 and text_tokens & _IDENT_NOUNS):
            # third-person assertions ("the user's name is X") are identity
            # statements too — requiring a first-person pronoun left them
            # without facet keys, so an incidental "file NAME must match..."
            # could outrank them depending on store ORDER (auditor finding,
            # 6.2.2, reproduced: name fact first -> distractor won)
            # stored facts earn only the facets they actually state —
            # never the whole identity-key set (auditor finding, 6.2.1)
            facets = _facet_keys(text_tokens)
            for k in (facets or sorted(IDENTITY_KEYS)):
                keys.setdefault(k)
        self._ensure_related()
        if self.related is not None:
            for w in list(keys):
                # expand RARE terms only: a term already frequent in the
                # corpus has direct hits, and expanding it just smears its
                # neighbours' vocabulary onto unrelated facts (auditor
                # finding: "name" imported the distractors' keys onto the
                # user's real name fact and onto identity queries)
                if self.related.df.get(stem(w.lower()), 0) >= 2:
                    continue
                for term, sc in self.related.related(w, top_k=4):
                    # identity keys are EARNED by stating identity, never
                    # imported by co-occurrence: expansion used to smear
                    # `user` onto a filename convention stored after the
                    # name fact (auditor finding, 6.2.2)
                    if sc >= 0.15 and term not in IDENTITY_KEYS:
                        keys.setdefault(term)
        return list(keys)[:24]

    @staticmethod
    def _clean_text(text):
        """Strip terminal control chars (keep newlines/tabs) so stored text
        can never carry ANSI escapes back to a terminal on recall. Shared by
        remember and link so their node ids agree (auditor finding: link
        hashed the raw text while remember hashed the cleaned text, creating
        a phantom edge that the dangling-edge filter then silently dropped)."""
        return re.sub(u"[\x00-\x08\x0b-\x1f\x7f\u202a-\u202e\u2066-\u2069]",
                      "", text or "").strip()

    # -- write path ---------------------------------------------------
    def remember(self, text, confidence=1.0):
        if not text or not text.strip():
            raise ValueError("cannot remember empty text")
        text = self._clean_text(text)
        if not text:
            raise ValueError("cannot remember control-characters-only text")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError("memory text exceeds %d chars — store the "
                             "document in a file and remember its path"
                             % MAX_TEXT_CHARS)
        nid = self._id(text)
        now = _now().isoformat()
        is_new = nid not in self.nodes
        if nid in self.nodes:
            n = self.nodes[nid]
            n["weight"] = min(1.0, n["weight"] + 0.2)
            n["peak_weight"] = max(n.get("peak_weight", 1.0), n["weight"])
            n["access_count"] = n.get("access_count", 0) + 1
            n["last_accessed"] = now
            n["confidence"] = max(n.get("confidence", 1.0), confidence)
            self._bumped[nid] = self._bumped.get(nid, 0) + 1
            # a re-remembered superseded fact is an explicit re-assertion:
            # the user says it IS true again — reopen a NEW validity
            # segment starting now (the closed segment stays queryable in
            # the journal; without this, `recall --at` would claim the
            # fact was true during the closed interval — auditor finding)
            if n.get("valid_to"):
                n["valid_to"] = None
                n.pop("superseded_by", None)
                self._clear_supersession_edges(nid)
                n["valid_from"] = now
                self._dirty.add(nid)
        else:
            by, session = self._actor()
            self.nodes[nid] = {
                "text": text,
                "weight": 1.0,
                "peak_weight": 1.0,
                "created": now,
                "last_accessed": now,
                "access_count": 0,
                "confidence": confidence,
                "keys": self._extract_keys(text),
                # provenance + truth validity, written the moment the
                # fact is learned (structure at write time)
                "origin": {"by": by, "session": session, "via": "remember"},
                "valid_from": now,
                "valid_to": None,
            }
            self.edges.setdefault(nid, {})
            self._dirty.add(nid)
            self.related = None    # rebuild lazily; avoids O(N^2) per write
        self._save()
        # journal AFTER the save: the provenance log records only facts
        # that actually landed on disk
        if is_new:
            self._journal("remember", id=nid, text=text)
        else:
            self._journal("remember", id=nid, dup=True)
        self._log_signal("remember", text)
        return nid

    def link(self, text_a, text_b, relation="related"):
        # relations end up in graph.json and journals — same control-char
        # hygiene as memory texts (auditor finding), plus a sane length cap
        relation = re.sub(r"[\x00-\x1f\x7f]", "", relation).strip()[:60] or "related"
        # hash the CLEANED text, exactly as remember() does, so the edge is
        # stored under the same id the node gets — otherwise the edge points
        # at a phantom id and is dropped on next load (auditor finding)
        text_a, text_b = self._clean_text(text_a), self._clean_text(text_b)
        id_a, id_b = self._id(text_a), self._id(text_b)
        # a self-loop would feed a node its own activation on every hop of
        # spreading recall, silently inflating its rank (auditor finding)
        if id_a == id_b:
            raise ValueError("cannot link a memory to itself")
        if id_a not in self.nodes:
            self.remember(text_a)
        if id_b not in self.nodes:
            self.remember(text_b)
        now = _now().isoformat()
        self.edges.setdefault(id_a, {})[id_b] = {"relation": relation,
                                                 "weight": 1.0, "created": now}
        self.edges.setdefault(id_b, {})[id_a] = {"relation": relation,
                                                 "weight": 1.0, "created": now}
        self._save()
        self._journal("link", id=id_a, other=id_b, relation=relation)
        self._log_signal("link", "%s --%s--> %s" % (text_a, relation, text_b))
        return "linked: %s <-> %s" % (text_a, text_b)

    @staticmethod
    def _content_tokens(text):
        """Raw stemmed content tokens — no expansion, no identity fallback.
        Used to gate destructive operations on real lexical overlap."""
        return {stem(w) for w in _tokenize((text or "").lower())
                if w not in STOPWORDS}

    def _clear_supersession_edges(self, nid):
        """A reopened fact is current again: drop the stale superseded-by /
        supersedes edge pair left over from its closed segment (the
        transition stays in the journal). Without this, a LIVE fact kept
        wearing a "superseded-by" edge — `why` reported it as both current
        and replaced (auditor finding, 6.2.2)."""
        for nbr, e in list(self.edges.get(nid, {}).items()):
            if e.get("relation") == "superseded-by":
                del self.edges[nid][nbr]
                self._pruned_edges.add((nid, nbr))
                rev = self.edges.get(nbr, {})
                if rev.get(nid, {}).get("relation") == "supersedes":
                    del rev[nid]
                    self._pruned_edges.add((nbr, nid))
                    if not rev:
                        del self.edges[nbr]
        if nid in self.edges and not self.edges[nid]:
            del self.edges[nid]

    def correct(self, old_hint, new_text):
        """Temporal reconsolidation (fusion, not erasure): find the memory
        best matching `old_hint`, CLOSE its validity (valid_to = now,
        superseded_by = new id) and create the corrected fact as a new
        node carrying the old text in its history, joined by an explicit
        `supersedes` edge. The state transition ("we were on MySQL, we
        moved to Postgres") stays in the graph through the grace window;
        after the closed fact archives, the lineage lives on in the
        successor's history entries and the permanent journal — recall
        simply stops returning the closed fact either way.

        Destructive-op gate: the match must share at least two content
        tokens with the hint (or cover half of a short hint) — a one-word
        coincidence must never rewrite an unrelated memory."""
        # same control-char hygiene and hashing as remember(): correct is a
        # write path too, and an uncleaned new_text would store text under
        # an id remember() would never produce (auditor finding). An empty
        # replacement would silently blank a memory — refuse it.
        new_text = self._clean_text(new_text)
        if not new_text:
            raise ValueError("corrected text must not be empty")
        if len(new_text) > MAX_TEXT_CHARS:
            raise ValueError("corrected text exceeds %d chars" % MAX_TEXT_CHARS)
        if not self.nodes:
            return None
        results, _, _ = self.recall(old_hint, top_k=1)
        if not results:
            return None
        nid, _, node = results[0]
        hint_toks = self._content_tokens(old_hint)
        shared = hint_toks & self._content_tokens(node["text"])
        if not (len(shared) >= 2 or
                (hint_toks and len(shared) / len(hint_toks) >= 0.5)):
            return None
        old_text = node["text"]
        now = _now().isoformat()
        new_nid = self._id(new_text)
        if new_nid == nid:                 # correcting to the same text
            return old_text
        lowered = round(node.get("confidence", 1.0) * 0.7, 3)
        entry = {"text": old_text, "replaced": now}
        existing = self.nodes.get(new_nid)
        if existing is not None:
            # the corrected text already exists: fuse into it
            existing.setdefault("history", []).append(entry)
            existing["confidence"] = min(existing.get("confidence", 1.0),
                                         lowered)
            existing["last_accessed"] = now
            if existing.get("valid_to"):   # re-asserted → new segment
                existing["valid_to"] = None
                existing.pop("superseded_by", None)
                self._clear_supersession_edges(new_nid)
                existing["valid_from"] = now
        else:
            by, session = self._actor()
            self.nodes[new_nid] = {
                "text": new_text,
                "weight": node.get("weight", 1.0),
                "peak_weight": node.get("peak_weight", 1.0),
                "created": now,
                "last_accessed": now,
                "access_count": 0,
                "confidence": lowered,
                "keys": self._extract_keys(new_text),
                "history": list(node.get("history", [])) + [entry],
                "origin": {"by": by, "session": session, "via": "correct"},
                "valid_from": now,
                "valid_to": None,
            }
        # fusion: the new fact inherits the old fact's KNOWLEDGE
        # connections — but never its supersession-transition edges: those
        # mark one specific pair's state change, and inheriting them gave a
        # node a second "superseded-by" edge contradicting its own
        # superseded_by field in `why` after an A->B->C->A chain (auditor
        # finding, 6.2.4; display-only, but provenance must not lie)
        for nbr, e in list(self.edges.get(nid, {}).items()):
            if nbr == new_nid:
                continue
            if e.get("relation") in ("supersedes", "superseded-by"):
                continue
            self.edges.setdefault(new_nid, {}).setdefault(nbr, dict(e))
            rev = self.edges.get(nbr, {}).get(nid)
            if rev is not None and rev.get("relation") not in (
                    "supersedes", "superseded-by"):
                self.edges.setdefault(nbr, {}).setdefault(new_nid, dict(rev))
        # the explicit, timestamped state transition
        self.edges.setdefault(new_nid, {})[nid] = {
            "relation": "supersedes", "weight": 0.5, "created": now}
        self.edges.setdefault(nid, {})[new_nid] = {
            "relation": "superseded-by", "weight": 0.5, "created": now}
        # close the old fact — invalidation is explicit and reversible,
        # never conflated with attention decay
        node["valid_to"] = now
        node["superseded_by"] = new_nid
        self._dirty.add(nid)
        self._dirty.add(new_nid)
        self.related = None
        self._save()
        self._journal("correct", old_id=nid, new_id=new_nid,
                      old_text=old_text, new_text=new_text)
        self._log_signal("correct", "%s => %s" % (old_text, new_text))
        return old_text

    # -- read path ------------------------------------------------------
    def recall(self, query, top_k=RECALL_TOP_K, max_hops=RECALL_RADIUS,
               rrf_k=60, at=None):
        """Spreading activation + IDF + Reciprocal Rank Fusion.

        Read-only by design: recall never writes to disk. Reinforcement
        happens through the separate bump() (called on confirmed hits),
        so health checks and repeated queries cannot skew the weights.

        Truth-validity filter: only facts valid AT `at` (default: now)
        are candidates. Superseded facts stay in the graph for lineage
        (`why`, `entity`, `recall --at <date>`) but are never returned
        as current answers."""
        t0 = time.perf_counter()
        q_tokens = set(re.findall(r"[\w؀-ۿ]+", query.lower()))
        # a PURE identity question ("what is my name") carries a pronoun
        # and essentially no content terms; "أين الخادم الرئيسي" carries a
        # pronoun AND real content — only the former skips the rerank
        q_content = {t for t in q_tokens
                     if len(t) >= 3 and t not in STOPWORDS
                     and t not in PRONOUN_FALLBACK
                     and t not in IDENTITY_KEYS}
        identity_q = bool(q_tokens & PRONOUN_FALLBACK) and len(q_content) <= 1
        keys = set(self._extract_keys(query, is_query=True))
        if not keys:
            return [], 0.0, {}
        expanded = set(keys)
        if self.related is not None:
            for w in list(keys):
                if self.related.df.get(stem(w.lower()), 0) >= 2:
                    continue                     # same rare-term-only rule
                for term, _ in self.related.related(w, top_k=4):
                    if term in IDENTITY_KEYS:
                        continue
                    expanded.add(term)
        keys = expanded
        alive = {nid for nid, n in self.nodes.items()
                 if self._valid_at(n, at)}
        N = max(1, len(alive))

        df = defaultdict(int)
        for nid in alive:
            for k in set(self.nodes[nid].get("keys", [])):
                df[k] += 1
        idf = {k: math.log(1 + N / (1 + df.get(k, 0))) for k in keys}

        # direct channel: IDF-weighted key overlap + substring containment.
        # Weight biases the ranking but never vetoes it (floor at 0.35):
        # a decayed-but-exactly-matching memory must still beat fresh noise,
        # otherwise facts needed monthly can never earn their first
        # reinforcement (soak-test finding).
        direct = defaultdict(float)
        q_lower = query.lower()
        for nid in alive:
            node = self.nodes[nid]
            w_bias = 0.35 + 0.65 * node.get("weight", 1.0)
            n_keys = set(node.get("keys", []))
            shared = keys & n_keys
            if shared:
                direct[nid] += sum(idf[k] for k in shared) * w_bias
            n_text = node["text"].lower()
            substr = sum(1 for w in keys if len(w) >= 4 and w in n_text)
            reverse = sum(1 for k in n_keys if len(k) >= 4 and k in q_lower)
            if substr + reverse:
                direct[nid] += (substr + reverse) * 0.6 * w_bias

        # pattern completion: no direct hits -> fuzzy-match node texts so a
        # partial or misspelled cue can still reactivate the memory.
        if not direct and alive:
            for nid in alive:
                node = self.nodes[nid]
                sim = self.embedder.similarity(query, node["text"])
                if sim >= 0.25:
                    direct[nid] = sim * FUZZY_ACTIVATION * node.get("weight", 1.0)
        if not direct:
            return [], (time.perf_counter() - t0) * 1000, {}

        # spreading channel: propagate activation over edges
        spread = defaultdict(float)
        wave = dict(direct)
        for hop in range(max_hops + 1):
            nxt = defaultdict(float)
            for nid, act in wave.items():
                spread[nid] += act
                if hop < max_hops and act > SPREADING_THRESHOLD:
                    for nbr, ed in self.edges.get(nid, {}).items():
                        if nbr not in alive:   # closed facts don't relay
                            continue
                        nxt[nbr] += act * ACTIVATION_DECAY * ed.get("weight", 1.0) / (hop + 1)
            wave = nxt
            if not wave:
                break

        # RRF fusion: absent nodes get rank len(list)+1, not infinity, so the
        # spreading channel keeps real influence in the fused score.
        dr = {n: i for i, (n, _) in enumerate(
            sorted(direct.items(), key=lambda x: -x[1]))}
        sr = {n: i for i, (n, _) in enumerate(
            sorted(spread.items(), key=lambda x: -x[1]))}
        dr_default, sr_default = len(dr) + 1, len(sr) + 1
        fused = {}
        for nid in set(direct) | set(spread):
            fused[nid] = (1.0 / (rrf_k + dr.get(nid, dr_default)) +
                          1.0 / (rrf_k + sr.get(nid, sr_default)))

        # lexical-semantic re-rank of the head (offline hash embeddings).
        # Defense in depth: activation can only reach ids absent from
        # self.nodes if the graph was mutated externally mid-flight —
        # drop them instead of raising.
        fused = {nid: s for nid, s in fused.items() if nid in alive}
        ranked = sorted(fused.items(), key=lambda x: -x[1])
        # identity questions ("what is my name") are decided by lexical
        # identity evidence; the char-gram rerank favors token repetition
        # ("file name ... class name") and must sit this one out
        # (auditor finding)
        if len(ranked) > 1 and not identity_q:
            reranked = []
            for nid, base in ranked[:top_k * 3]:
                sim = self.embedder.similarity(query, self.nodes[nid]["text"])
                reranked.append((nid, base * (1.0 + sim)))
            reranked.sort(key=lambda x: -x[1])
            ranked = reranked

        # pattern separation: drop near-duplicate results from the head so
        # top-k answers cover distinct memories, not one memory five ways.
        selected = []
        for nid, score in ranked:
            dup = False
            for snid, _ in selected:
                if self.embedder.similarity(
                        self.nodes[nid]["text"], self.nodes[snid]["text"]) >= SEPARATION_SIM:
                    dup = True
                    break
            if not dup:
                selected.append((nid, score))
            if len(selected) >= top_k:
                break

        results = [(nid, score, self.nodes[nid]) for nid, score in selected]
        kinds = {nid: ("direct" if nid in direct else "trace")
                 for nid, _, _ in results}
        return results, (time.perf_counter() - t0) * 1000, kinds

    def bump(self, node_ids):
        """Reinforce nodes after a confirmed recall (kept separate from
        recall() so reads stay pure; the `confirm` CLI command is the
        agent-facing path here). Tracks peak_weight for Ebbinghaus, and
        restrengthens the confirmed node's edges — connections you actually
        use stay strong, unused ones decay away dream by dream."""
        now = _now()
        changed = False
        for nid in node_ids:
            if nid in self.nodes:
                n = self.nodes[nid]
                n["access_count"] = n.get("access_count", 0) + 1
                n["weight"] = min(1.0, n["weight"] + BOOST_PER_ACCESS)
                n["peak_weight"] = max(n.get("peak_weight", 1.0), n["weight"])
                n["last_accessed"] = now.isoformat()
                for nbr, e in self.edges.get(nid, {}).items():
                    e["weight"] = min(1.0, e.get("weight", 1.0) + EDGE_BOOST)
                    rev = self.edges.get(nbr, {}).get(nid)
                    if rev is not None:
                        rev["weight"] = e["weight"]
                self._bumped[nid] = self._bumped.get(nid, 0) + 1
                changed = True
        if changed:
            self._save()
            self._journal("confirm", ids=[nid for nid in node_ids
                                          if nid in self.nodes])
        return changed

    def decay(self, dry_run=False):
        """Ebbinghaus forgetting curve: R = e^(-t/S).

        Stability S grows with each confirmed recall, so frequently used
        memories decay slowly while one-off trivia fades fast. Nodes that
        fall below WEIGHT_THRESHOLD with < 2 recalls are pruned — but never
        within GRACE_DAYS of their last access (a fact noted today and
        needed next month must survive to its first recall), and never
        destroyed: pruned texts are archived to .mind/archive.md."""
        now = _now()
        pruned = []
        for nid in list(self.nodes.keys()):
            n = self.nodes[nid]
            # superseded facts are CLOSED states, not competing memories:
            # they don't decay against the living, and once their closure
            # ages past the grace window they archive regardless of
            # access_count — their lineage stays in history entries, the
            # supersedes edge, and the permanent journal
            vt = n.get("valid_to")
            if vt:
                try:
                    closed_days = (now - datetime.fromisoformat(vt)).days
                except (TypeError, ValueError):
                    closed_days = 0
                if closed_days > GRACE_DAYS:
                    pruned.append((nid, "[superseded] " + n["text"]))
                continue
            try:
                days = (now - datetime.fromisoformat(n["last_accessed"])).days
            except (TypeError, ValueError):
                # TypeError too: an in-memory non-string timestamp (mutated
                # after load bypasses _load's repair) must degrade to
                # "fresh", not crash the whole dream (auditor finding)
                days = 0
            # a future last_accessed (clock skew / cross-machine sync — this
            # IS synced agent memory, and _now() is naive local time) makes
            # `days` negative and retention explode past 1.0, inflating the
            # weight unboundedly and permanently. Treat future as fresh.
            days = max(0, days)
            access = n.get("access_count", 0)
            stability = STABILITY_BASE_DAYS + access * STABILITY_PER_ACCESS
            retention = math.exp(-days / stability)
            # clamp to [0,1] like every other weight-mutating path (auditor
            # finding: decay was the only one without an upper clamp)
            new_weight = max(0.0, min(1.0, n.get("peak_weight", 1.0) * retention))
            if not dry_run:
                n["weight"] = new_weight
                self._decayed[nid] = new_weight
            if new_weight < WEIGHT_THRESHOLD and access < 2 and days > GRACE_DAYS:
                pruned.append((nid, n["text"]))
        if dry_run:
            return [t for _, t in pruned]
        # archive FIRST, delete only what was durably archived: if the
        # archive cannot be written (e.g. someone symlinked archive.md),
        # nothing is pruned — "archived, not destroyed" is a guarantee,
        # not a best effort (auditor finding).
        if pruned:
            if self._archive([t for _, t in pruned], now):
                for nid, _ in pruned:
                    del self.nodes[nid]
                    self._deleted.add(nid)
                    self.edges.pop(nid, None)
                    for other in self.edges.values():
                        other.pop(nid, None)
                # pruning is a fact-lifecycle event too: the journal keeps
                # the id→text mapping even after the node leaves the graph
                self._journal("prune", ids=[nid for nid, _ in pruned],
                              texts=[t for _, t in pruned])
            else:
                print("warning: archive.md is not writable (symlink?); "
                      "keeping %d prunable memories." % len(pruned),
                      file=sys.stderr)
                pruned = []
        self._save()
        return [t for _, t in pruned]

    def _archive(self, texts, now):
        """Forgotten, not destroyed: pruned memories append to archive.md.
        Returns True only when the archive write actually happened."""
        arch = self.path.parent / "archive.md"
        if arch.is_symlink():
            return False
        lines = ["\n## forgotten on %s\n" % now.date()]
        lines += ["- %s" % t for t in texts]
        header = "" if arch.exists() else \
            "# mind archive — memories pruned by decay (restore with `remember`)\n"
        # APPEND, don't rewrite: the archive only grows, and rewriting the
        # whole file per prune batch is O(archive) forever (auditor
        # finding). Same trust-boundary checks as every other write.
        try:
            _reject_symlinked_parents(arch, self.path.parent)
            fd = os.open(str(arch),
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND |
                         getattr(os, "O_NOFOLLOW", 0), 0o644)
            try:
                os.write(fd, (header + "\n".join(lines) + "\n")
                         .encode("utf-8"))
                os.fsync(fd)    # archived text is a durability promise
            finally:
                os.close(fd)
        except (OSError, ValueError):
            return False
        return True

    def _log_signal(self, kind, content):
        # same O_NOFOLLOW discipline as every other write path: the
        # is_symlink() check alone is TOCTOU-raceable (auditor finding,
        # 6.2.1 — this was the one append still using a plain open())
        sig_file = self.path.parent / SIGNALS_FILE
        if sig_file.is_symlink():
            return
        try:
            _reject_symlinked_parents(sig_file, self.path.parent)
            fd = os.open(str(sig_file),
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND |
                         getattr(os, "O_NOFOLLOW", 0), 0o644)
            try:
                os.write(fd, (json.dumps(
                    {"kind": kind, "content": content,
                     "ts": _now().isoformat()},
                    ensure_ascii=False) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except (OSError, ValueError):
            pass    # telemetry only — never block the write it rode on

    # -- provenance -----------------------------------------------------
    @staticmethod
    def _actor():
        """Who is writing. Agents/harnesses set MIND_BY and MIND_SESSION
        in the environment; the zero-setup default is 'agent'."""
        return (os.environ.get("MIND_BY", "agent"),
                os.environ.get("MIND_SESSION"))

    def _journal(self, op, **fields):
        """Append-only provenance log (journal.jsonl). Unlike
        signals.jsonl (telemetry, cleared by dream), the journal is NEVER
        rotated or deleted: every fact-mutating operation records who,
        when, and what, so "where did this fact come from" stays
        answerable for the life of the project. Journal failure warns but
        never blocks a memory write (availability over completeness —
        documented tradeoff)."""
        jf = self.path.parent / JOURNAL_FILE
        # same trust boundary as every other write: a symlinked journal
        # OR a symlinked .mind root must never leak a file outside the
        # project (the lock file had this exact hole once — test finding)
        try:
            _reject_symlinked_parents(jf, self.path.parent)
        except ValueError:
            print("warning: .mind is unsafe (symlink?); skipping "
                  "provenance entry.", file=sys.stderr)
            return
        if jf.is_symlink():
            print("warning: journal.jsonl is a symlink; skipping "
                  "provenance entry.", file=sys.stderr)
            return
        by, session = self._actor()
        entry = {"ts": _now().isoformat(), "op": op, "by": by}
        if session:
            entry["session"] = session
        entry.update(fields)
        try:
            # single O_APPEND os.write: concurrent writers cannot
            # interleave a line on a local filesystem (auditor finding:
            # the provenance log was the one unlocked write path)
            fd = os.open(str(jf), os.O_WRONLY | os.O_CREAT | os.O_APPEND |
                         getattr(os, "O_NOFOLLOW", 0), 0o644)
            try:
                os.write(fd, (json.dumps(entry, ensure_ascii=False)
                              + "\n").encode("utf-8"))
                os.fsync(fd)    # provenance is permanent — survive power loss
            finally:
                os.close(fd)
        except OSError as e:
            print("warning: journal.jsonl not writable (%s); provenance "
                  "entry lost." % e, file=sys.stderr)

    def journal_entries(self, node_id=None, tail_bytes=10_000_000):
        """Read the provenance log, optionally filtered to one node.
        The log is append-only and never cleared, so reads are capped to
        the last `tail_bytes` (10MB ≈ years of normal use) — `why` and
        `status` must not O(file) forever (auditor finding)."""
        jf = self.path.parent / JOURNAL_FILE
        if not jf.exists():
            return []
        size = jf.stat().st_size
        with jf.open("rb") as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                f.readline()                    # skip the partial line
                print("note: journal is %.1f MB; reading the last 10 MB."
                      % (size / 1e6), file=sys.stderr)
            data = f.read().decode("utf-8", "replace")
        out = []
        for line in data.splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if node_id is None or node_id in (e.get("id"), e.get("old_id"),
                                              e.get("new_id")) \
                    or node_id in (e.get("ids") or []):
                out.append(e)
        return out

    # -- temporal validity ----------------------------------------------
    @staticmethod
    def _valid_at(node, at=None):
        """Truth validity, distinct from attention (weight): a fact is
        valid from valid_from until valid_to (open = still true). ISO
        strings compare lexicographically, so no parsing is needed.

        Present-time checks tolerate a slightly-future valid_from (26 h —
        covers every timezone offset plus drift): timestamps are naive
        local time, so memory synced from a machine east of this one
        carries "future" stamps. decay() already clamps future elapsed
        time to zero; without the same tolerance here a fresh synced fact
        was invisible until local midnight caught up (auditor finding,
        6.2.1). Explicit --at queries stay literal: history is history."""
        vf = node.get("valid_from") or node.get("created") or ""
        vt = node.get("valid_to")
        if at is None:
            # symmetric skew handling: a CLOSING stamped by an eastern
            # machine (vt in our future) means the fact was already
            # superseded there — treating it as still-valid here returned
            # BOTH the old and new fact until local midnight (auditor
            # finding, 6.2.2). Both bounds compare against the horizon.
            # Known, accepted divergence: within the <=26h skew window a
            # present-time check and a literal `--at <now>` can disagree
            # about a future-stamped fact — the present view prefers the
            # synced machines' consensus, --at stays literal history.
            horizon = (_now() + timedelta(hours=26)).isoformat()
            return vf <= horizon and (vt is None or horizon < vt)
        return vf <= at and (vt is None or at < vt)


# ────────────────────────────────────────────────────────────────
# Layer 3: Cortex — consolidated durable knowledge
# ────────────────────────────────────────────────────────────────
class Cortex:
    def __init__(self, path):
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)

    def files(self):
        return sorted(self.path.glob("*.md"))

    def promote(self, topic, content):
        base = re.sub(r'[^\w؀-ۿ]+', '_', topic).strip('_')[:40]
        fname = (base or "topic") + ".md"
        fpath = self.path / fname
        # two distinct topics can sanitize to the same filename — never
        # silently overwrite "durable" knowledge about a different topic
        # (auditor finding): disambiguate with a short content hash.
        if fpath.exists():
            first = fpath.read_text("utf-8").splitlines()[:1]
            if first and first[0] != "# %s" % topic:
                suffix = hashlib.md5(topic.encode("utf-8")).hexdigest()[:6]
                fpath = self.path / ("%s-%s.md" % (base or "topic", suffix))
        header = "# %s\n\n> promoted by dream on %s\n\n" % (topic, _now().date())
        # boundary = .mind/ so a symlinked cortex/ dir can't redirect the
        # write outside the project (auditor finding)
        _atomic_write(fpath, header + content + "\n", boundary=self.path.parent)
        return str(fpath.relative_to(self.path.parent))


# ────────────────────────────────────────────────────────────────
# The Dreamer — sleep cycle between sessions
# ────────────────────────────────────────────────────────────────
class Dreamer:
    """light sleep (ingest signals) -> deep sleep (Ebbinghaus decay +
    synaptic pruning) -> REM (cluster, promote, detect contradictions).

    Fully deterministic: no LLM calls, no network, every action explained
    in the dream journal. Run with dry_run=True to preview."""

    def __init__(self, mind_dir, hippo, cortex):
        self.dir = mind_dir / DREAMS_DIR
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # a dangling-symlink dreams/ raises EEXIST here (exists() is
            # False so exist_ok can't swallow it). Constructing the Dreamer
            # must never take down unrelated commands — dream() itself
            # already refuses the unsafe journal write gracefully.
            pass
        self.hippo = hippo
        self.cortex = cortex
        self.signals_file = mind_dir / SIGNALS_FILE

    def dream(self, dry_run=False):
        mode = " (dry run — nothing written)" if dry_run else ""
        log = ["# Dream journal — %s%s" % (_now().date(), mode), ""]
        log.append("_cycle started %s_" % _now().strftime("%H:%M"))

        # 1. light sleep: count the session's write signals (telemetry). The
        # consolidation inputs are the node/edge weights themselves, not the
        # signal log — so this is reported, then cleared, not replayed.
        signals = self._read_signals()
        log.append("\n## Light sleep\nSaw %d session signals "
                   "(telemetry; consolidation runs on the graph weights)."
                   % len(signals))

        # 2. deep sleep: Ebbinghaus decay + node pruning
        pruned = self.hippo.decay(dry_run=dry_run)
        log.append("\n## Deep sleep")
        log.append("- decay: %s %d weak nodes."
                   % ("would prune" if dry_run else "pruned", len(pruned)))
        for t in pruned[:5]:
            log.append("  - forgot: %s" % t)
        if len(pruned) > 5:
            log.append("  - ... and %d more" % (len(pruned) - 5))

        # synaptic homeostasis: edges weaken a little ONCE PER CALENDAR DAY
        # (the first dream of the day), not once per cycle — auto-dream can
        # legitimately run several cycles in a busy day, and per-cycle decay
        # compounded (0.95^n) fast enough to prune healthy edges in days
        # instead of the documented ~45 nights (auditor finding, 6.2.1).
        # Edges of memories that earn confirmed recalls get restrengthened
        # by bump(), so only genuinely unused connections drift down...
        # the once-a-day marker is persisted INSIDE graph.json — the old
        # journal-file-existence heuristic re-decayed whenever the day's
        # memo was deleted or lost (auditor finding, 6.2.2)
        today = str(_now().date())
        first_cycle_today = self.hippo.meta.get("last_edge_decay", "") < today
        if not dry_run and first_cycle_today:
            for nbrs in self.hippo.edges.values():
                for e in nbrs.values():
                    e["weight"] = round(e.get("weight", 1.0) * EDGE_DECAY_PER_DREAM, 4)
            self.hippo.meta["last_edge_decay"] = today
        # ...and synaptic pruning removes the ones that decayed away
        pruned_edges = 0
        for nid in list(self.hippo.edges.keys()):
            for neighbor in list(self.hippo.edges[nid].keys()):
                if self.hippo.edges[nid][neighbor].get("weight", 1.0) < EDGE_PRUNE_THRESHOLD:
                    if not dry_run:
                        del self.hippo.edges[nid][neighbor]
                        self.hippo._pruned_edges.add((nid, neighbor))
                    pruned_edges += 1
            if not dry_run and nid in self.hippo.edges and not self.hippo.edges[nid]:
                del self.hippo.edges[nid]
        if pruned_edges:
            log.append("- synaptic pruning: %s %d weak edges."
                       % ("would remove" if dry_run else "removed", pruned_edges))
        # persist the edge-weight changes: decay() only saves when a node is
        # pruned, so on a steady-state night (no node/conflict change) the
        # in-memory edge decay would be discarded and each fresh `dream`
        # process would reload weight 1.0 and re-decay to no effect — edges
        # would never actually weaken or prune across real runs (auditor
        # finding: the claim was true in-process, false on disk).
        if not dry_run:
            self.hippo._save()

        # 3. REM: cluster related memories and promote recurring themes.
        # Clustering uses offline hash embeddings — deterministic, no network.
        promoted = self._rem_promote(log, dry_run)

        # 4. REM: contradiction scan (feeds reconsolidation)
        conflicts = self._rem_conflicts(log, dry_run)

        log.append("\n## Summary")
        log.append("- nodes: %d | pruned: %d | promoted clusters: %d | "
                   "conflicts flagged: %d"
                   % (len(self.hippo.nodes), len(pruned), len(promoted),
                      len(conflicts)))

        memo_text = "\n".join(log) + "\n"
        if dry_run:
            return None, memo_text
        memo = self.dir / ("%s.md" % _now().date())
        # boundary = .mind/ so a symlinked dreams/ dir can't redirect the
        # journal write outside the project (auditor finding). If the dir is
        # unsafe, the consolidation already happened — just skip the journal
        # rather than crash with a traceback.
        try:
            # a second dream on the same date APPENDS its cycle to the day's
            # journal instead of silently replacing it (auditor finding:
            # only the last cycle of the day used to survive). The append
            # is a single O_APPEND os.write — same pattern as the archive —
            # because concurrent auto-dreams from parallel write commands
            # each land whole; the old read-modify-rewrite raced and
            # dropped sibling cycles (auditor finding, 6.2.0 wave)
            if memo.is_symlink():
                raise ValueError("dream journal is a symlink")
            _reject_symlinked_parents(memo, self.hippo.path.parent)
            payload = memo_text
            if memo.exists():
                payload = "\n---\n\n" + memo_text
            fd = os.open(str(memo),
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND |
                         getattr(os, "O_NOFOLLOW", 0), 0o644)
            try:
                os.write(fd, payload.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        except ValueError:
            print("warning: .mind/dreams is unsafe (symlink?); "
                  "skipping dream journal for this run.", file=sys.stderr)
            return None, memo_text
        if self.signals_file.exists() and not self.signals_file.is_symlink():
            self.signals_file.unlink()
        return str(memo.relative_to(self.hippo.path.parent)), memo_text

    def _rem_promote(self, log, dry_run):
        emb = self.hippo.embedder
        clusters = []
        for nid, n in self.hippo.nodes.items():
            if not self.hippo._valid_at(n):   # closed facts don't cluster
                continue
            placed = False
            for c in clusters:
                if emb.similarity(n["text"], c["centroid"]) > CLUSTER_SIM:
                    c["members"].append(nid)
                    placed = True
                    break
            if not placed:
                clusters.append({"centroid": n["text"], "members": [nid]})
        promoted = []
        log.append("\n## REM — consolidation")
        for c in clusters:
            if len(c["members"]) >= PROMOTION_THRESHOLD:
                texts = [self.hippo.nodes[m]["text"] for m in c["members"][:5]]
                topic = c["centroid"][:50]
                if not dry_run:
                    try:
                        self.cortex.promote(topic, "\n".join("- %s" % t for t in texts))
                    except ValueError:
                        # symlinked cortex/ dir: skip promotion rather than
                        # crash the whole dream (auditor finding)
                        log.append("  - (skipped promotion: cortex dir unsafe)")
                        continue
                promoted.append(topic)
                log.append("- %s cluster (%d memories) -> cortex: %s"
                           % ("would promote" if dry_run else "promoted",
                              len(c["members"]), topic))
        if not promoted:
            log.append("- no cluster reached the promotion threshold (%d)."
                       % PROMOTION_THRESHOLD)
        return promoted

    def _rem_conflicts(self, log, dry_run):
        """Deterministic contradiction scan: two memories about the same
        subject (shared rare keys) that are similar but not near-identical
        are flagged and linked, never auto-deleted. The user (or agent)
        resolves them with `mind correct`."""
        emb = self.hippo.embedder
        # a superseded fact conflicting with its successor is not a
        # contradiction — it's history. Scan only currently-valid facts.
        nodes = [(nid, n) for nid, n in self.hippo.nodes.items()
                 if self.hippo._valid_at(n)]
        N = max(1, len(nodes))
        df = defaultdict(int)
        for _, n in nodes:
            for k in set(n.get("keys", [])):
                df[k] += 1
        conflicts = []
        log.append("\n## REM — contradiction scan")
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                ida, a = nodes[i]
                idb, b = nodes[j]
                rare_shared = [k for k in set(a["keys"]) & set(b["keys"])
                               if df[k] <= max(2, N // 4)]
                if len(rare_shared) < 2:
                    continue
                sim = emb.similarity(a["text"], b["text"])
                if 0.35 <= sim < 0.9:
                    conflicts.append((ida, idb))
                    if not dry_run:
                        now_iso = _now().isoformat()
                        self.hippo.edges.setdefault(ida, {})[idb] = {
                            "relation": "possible-conflict", "weight": 0.5,
                            "created": now_iso}
                        self.hippo.edges.setdefault(idb, {})[ida] = {
                            "relation": "possible-conflict", "weight": 0.5,
                            "created": now_iso}
                    log.append("- possible conflict (sim %.2f):" % sim)
                    log.append("    a: %s" % a["text"][:80])
                    log.append("    b: %s" % b["text"][:80])
                    log.append("    resolve with: mind correct \"<wrong>\" \"<right>\"")
        if not conflicts:
            log.append("- none found.")
        elif not dry_run:
            self.hippo._save()
        return conflicts

    def _read_signals(self):
        # symlink/size guards on the READ side too: dream must not follow
        # a symlinked signals file or slurp an absurdly large one
        # (auditor finding)
        if not self.signals_file.exists() or self.signals_file.is_symlink():
            return []
        if self.signals_file.stat().st_size > 5_000_000:
            print("warning: signals.jsonl is suspiciously large; "
                  "ignoring it this cycle.", file=sys.stderr)
            return []
        out = []
        for line in self.signals_file.read_text("utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def _invocation(project_root=None):
    """The exact command an agent must type to reach THIS mind.py.

    The exported doctrine used to hardcode `python3 mind.py ...` — which
    silently fails for every user who keeps mind.py anywhere but the project
    root (field finding: an agent read the instructions, ran the command,
    got 'No such file', and gave up — memory stayed empty for a whole day).
    Relative form is kept when the script lives anywhere inside the
    project tree (shorter, runnable from the project root, and survives
    the project being moved); absolute otherwise.
    """
    try:
        script = Path(sys.argv[0]).resolve()
    except (OSError, ValueError):
        return "python3 mind.py"
    if script.name != "mind.py":        # imported (tests) or odd embedding
        return "python3 mind.py"
    if project_root is not None:
        try:
            rel = script.relative_to(Path(project_root).resolve())
            cmd = str(rel)
        except ValueError:
            cmd = str(script)
    else:
        cmd = str(script)
    if " " in cmd:
        cmd = '"%s"' % cmd
    return "python3 %s" % cmd


# ────────────────────────────────────────────────────────────────
# Layer 1: Working memory — always-on context + agent export
# ────────────────────────────────────────────────────────────────
class Active:
    BEGIN = "<!-- mind:memory begin (auto-generated, do not edit) -->"
    END = "<!-- mind:memory end -->"
    # Always written: the three canonical cross-agent files.
    CANONICAL = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
    # Written only when the project already uses that tool (the rule file —
    # or for Roo, the .roo/ directory — exists). Keeps fresh projects clean.
    DOT_TARGETS = (".cursorrules", ".windsurfrules", ".clinerules",
                   ".roo/rules/mind.md")
    TARGETS = CANONICAL + DOT_TARGETS

    def __init__(self, mind_dir, hippo, cortex):
        self.dir = mind_dir
        self.hippo = hippo
        self.cortex = cortex
        self.path = mind_dir / ACTIVE_FILE

    def generate(self, project_root):
        # working memory shows only facts that are currently TRUE:
        # superseded facts keep their lineage in the graph but never
        # occupy the agent's always-on context
        nodes_sorted = sorted((n for n in self.hippo.nodes.values()
                               if self.hippo._valid_at(n)),
                              key=lambda n: -n["weight"])
        hot, used = [], 0
        for n in nodes_sorted:
            line = "- %s" % n["text"]
            # the budget constant is already in characters (~200 tokens);
            # a leftover ×4 token→char conversion applied on top let the
            # hot list grow to 4× the documented working-memory size
            # (auditor finding)
            if used + len(line) > ACTIVE_TOKEN_BUDGET:
                continue    # skip oversized memories, keep filling the budget
            hot.append(line)
            used += len(line)
            if len(hot) >= 8:
                break
        cortex_files = ["- `cortex/%s`" % f.name for f in self.cortex.files()[:6]]
        inv = _invocation(project_root)
        # The standing-orders doctrine below is the write-side automation:
        # it rides the one channel every coding agent already auto-loads
        # (AGENTS.md / CLAUDE.md / GEMINI.md), the same way Hermes puts its
        # memory contract in the always-on system prompt and OpenClaw puts
        # "Write It Down" in its workspace AGENTS.md. Save-triggers + an
        # aggressive never-save list + declarative-not-imperative phrasing
        # + recall-before-ignorance are the four elements both proved out.
        content = """# ACTIVE.md — mind working memory

> auto-generated %s — do not edit by hand (memory tool: `%s ...`).

## Standing orders — memory is part of the job, not a request
You (the agent) maintain this project's memory AUTOMATICALLY while you work.
Nothing survives a session unless it is saved here — "mental notes" are lost
on restart. Never ask the user for permission to remember/recall/confirm:
these are your normal duties; do them and mention it in one short line.

**Save immediately** — `%s remember "the fact"` — when:
- the user states a preference, correction, decision, or STABLE personal/team detail
- you learn a stable fact about the environment, stack, conventions, or a tool quirk
- you solved something whose lesson will matter beyond this session
One fact per memory: split a braindump into atomic facts (several remember
commands chained in one shell call is fine) — composite blobs recall poorly.
**Before finishing any substantive task:** save the 1-3 durable facts it taught you.
**Session ending, or context about to be compacted?** Save durable facts FIRST.

**Never save** (rot is worse than forgetting): task progress, TODO state,
"fixed bug X", PR/issue numbers, commit SHAs, file counts — anything stale
within a week or trivially re-discoverable.
Phrase memories as declarative facts, not instructions to yourself:
"project uses pytest" ✓ — "always run pytest" ✗.

**Recall before claiming ignorance:** asked about prior work, decisions,
people, dates, or preferences? Run `%s recall "the question"` BEFORE saying
you don't know. Reinforce hits that actually answered you:
`%s confirm <id>` (ids are printed by recall).
A stored fact turned out wrong? `%s correct "old hint" "corrected fact"`
(supersedes cleanly — never remember a duplicate alongside it).
Two facts belong together? `%s link "a" "b" "relation"`.

## Hot memories (highest weight now)
%s

## Cortex index (consolidated knowledge)
%s

## Memory health
%s
- maintenance is self-running: after your writes, a dream cycle (decay,
  dedup, promotion, conflict scan) fires automatically when due — no cron
  needed. `%s dream` forces one; journal lands in `.mind/dreams/`.
""" % (_now().strftime("%Y-%m-%d %H:%M"), inv,
            inv, inv, inv, inv, inv,
            "\n".join(hot) if hot else "- (memory is empty — save the first fact NOW: stack, conventions, who the user is)",
            "\n".join(cortex_files) if cortex_files else "- (no cortex yet)",
            self._health_line(), inv)
        # boundary = .mind/ so a symlinked parent can't redirect the write
        _atomic_write(self.path, content, boundary=self.path.parent)
        return str(self.path.relative_to(project_root))

    def _health_line(self):
        """One status line the agent sees every session (the Hermes
        capacity-header idea: visible state drives correct behavior)."""
        total = len(self.hippo.nodes)
        valid = sum(1 for n in self.hippo.nodes.values()
                    if self.hippo._valid_at(n))
        last = "never"
        ddir = self.dir / DREAMS_DIR
        if ddir.exists():
            days = sorted(p.stem for p in ddir.glob("????-??-??.md"))
            if days:
                last = days[-1]
        return ("- %d memories (%d currently true) · last dream: %s"
                % (total, valid, last))

    def export_to_agents(self, project_root):
        """Write the working-memory block into every agent's instruction file,
        preserving any user content outside the guard markers."""
        src = self.path.read_text("utf-8") if self.path.exists() else ""
        written = []
        for target in self.TARGETS:
            target_path = Path(target)
            tpath = project_root / target_path
            # opt-in dot targets: written only for projects already using
            # that tool (the rule file — or .roo/ for Roo — is present)
            if target in self.DOT_TARGETS:
                anchor = project_root / target_path.parts[0]
                if not (anchor.exists() or anchor.is_symlink()):
                    continue
            parent = project_root
            skip = False
            for part in target_path.parent.parts:
                parent = parent / part
                # is_symlink() alone: exists() follows links, so a dangling
                # symlink parent would slip past an exists()-guarded check
                if parent.is_symlink():
                    written.append("%s (skipped: symlink parent)" % target)
                    skip = True
                    break
            if skip:
                continue
            if tpath.is_symlink():
                written.append("%s (skipped: symlink)" % target)
                continue
            tpath.parent.mkdir(parents=True, exist_ok=True)
            user_content = ""
            if tpath.exists():
                content = tpath.read_text("utf-8")
                # OUR block is identified structurally (BEGIN marker whose
                # body starts with our exact generated header), never by a
                # bare marker string: users legitimately quote the marker
                # syntax in fenced docs, and split-on-first/last silently
                # destroyed everything in between (auditor finding, wave 2)
                ours = -1
                idx = content.find(self.BEGIN)
                while idx != -1:
                    body = content[idx + len(self.BEGIN):].lstrip("\n")
                    if body.startswith("# ACTIVE.md — mind working memory"):
                        ours = idx
                        break
                    idx = content.find(self.BEGIN, idx + 1)
                if ours != -1:
                    j = content.find(self.END, ours)
                    before = content[:ours]
                    after = (content[j + len(self.END):] if j != -1 else "")
                    user_content = (before + after).strip()
                    # strip our own separator artifacts so re-export is idempotent
                    user_content = re.sub(
                        r'^---\s*\n<!-- user content below -->\s*\n?', '',
                        user_content).strip()
                else:
                    stripped = content.strip()
                    # Do not re-ingest our own stale block as "user content".
                    # This MUST be a structural match on our exact generated
                    # header — a bare substring test ("mind working memory")
                    # would classify any user file that merely mentions the
                    # tool as our output and silently discard the whole file
                    # (auditor finding: HIGH — a real CLAUDE.md saying "run
                    # mind.py" was destroyed with no backup or warning).
                    if stripped.startswith("# ACTIVE.md — mind working memory"):
                        user_content = ""
                    else:
                        user_content = stripped
            block = "%s\n%s\n%s" % (self.BEGIN, src, self.END)
            if user_content:
                new_content = "%s\n\n---\n<!-- user content below -->\n%s\n" % (
                    block, user_content)
                written.append("%s (memory + preserved content)" % target)
            else:
                new_content = block + "\n"
                written.append("%s (memory)" % target)
            _atomic_write(tpath, new_content)
        return written


# ────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────
class Mind:
    def __init__(self, project_root=None):
        self.root = Path(project_root or os.getcwd()).resolve()
        self.dir = self.root / MIND_DIR
        self.hippo = None
        self.cortex = None
        self.dreamer = None
        self.active = None

    def init(self):
        # BEFORE any mkdir: a symlinked .mind would let init create
        # cortex/dreams directories outside the project (auditor finding)
        if self.dir.is_symlink():
            raise ValueError("refusing: .mind is a symlink")
        if (self.dir / GRAPH_FILE).exists():
            print("mind memory already exists in %s (nothing changed)." % self.dir)
            print("  to reset: delete .mind/ first. for a report: python3 mind.py status")
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / CORTEX_DIR).mkdir(exist_ok=True)
        (self.dir / DREAMS_DIR).mkdir(exist_ok=True)
        self.hippo = Hippocampus(self.dir / GRAPH_FILE)
        self.hippo._save()
        self.cortex = Cortex(self.dir / CORTEX_DIR)
        self.active = Active(self.dir, self.hippo, self.cortex)
        self.active.generate(self.root)
        self.active.export_to_agents(self.root)
        print("""created mind memory in %s

layers:
  .mind/ACTIVE.md    working memory (always in agent context)
  .mind/graph.json   hippocampus (weighted concept graph)
  .mind/cortex/      cortex (consolidated knowledge)
  .mind/dreams/      dream journals
  .mind/signals.jsonl session signals

agent files exported (each carries the standing-orders contract that makes
your agent save and recall memories automatically, without being asked):
  AGENTS.md   (Kimi Code, Codex, Cursor, Zed, zcode, ...)
  CLAUDE.md   (Claude Code)
  GEMINI.md   (Gemini CLI)
  (.cursorrules / .windsurfrules / .clinerules / .roo/rules/mind.md
   are adopted automatically when the project already uses them)

automatic from here on:
  - your agent reads the contract every session and saves/recalls on its own
  - consolidation self-runs: writes trigger a dream cycle when due (no cron)

manual commands (optional):  %s remember/recall/dream/status""" % (
            self.dir, _invocation(self.root)))

    def _ensure(self):
        if self.dir.is_symlink():
            raise ValueError("refusing: .mind is a symlink")
        if not self.dir.exists():
            print("no mind memory here. run: python3 mind.py init", file=sys.stderr)
            sys.exit(1)
        self.hippo = Hippocampus(self.dir / GRAPH_FILE)
        self.cortex = Cortex(self.dir / CORTEX_DIR)
        self.dreamer = Dreamer(self.dir, self.hippo, self.cortex)
        self.active = Active(self.dir, self.hippo, self.cortex)

    def _auto_dream(self):
        """Self-running consolidation — the `git gc --auto` pattern.

        Hermes consolidates in-turn via its char-budget nudge; OpenClaw runs
        a nightly dreaming cron. An external CLI can rely on neither (no
        agent loop of ours, and containers/CI rarely have cron), so mind
        piggybacks maintenance on the writes themselves: after a write
        command, a full dream cycle fires when enough session signals have
        accumulated (>= AUTO_DREAM_SIGNALS) or the last dream is from a
        previous calendar day (daily cadence — the date-granular check
        means a just-before-midnight dream can re-fire after midnight,
        which only errs toward consolidating sooner). Failures never
        break the write that triggered it.
        Kill switch: MIND_AUTO_DREAM=0.
        """
        if os.environ.get("MIND_AUTO_DREAM", "1").lower() in ("0", "false", "no"):
            return False
        try:
            pending = 0
            sig = self.dir / SIGNALS_FILE
            if sig.exists() and not sig.is_symlink() \
                    and sig.stat().st_size <= 5_000_000:
                pending = sum(1 for ln in sig.read_text("utf-8").splitlines()
                              if ln.strip())
            if pending == 0:
                return False
            last_date = None
            ddir = self.dir / DREAMS_DIR
            if ddir.exists():
                days = sorted(p.stem for p in ddir.glob("????-??-??.md"))
                if days:
                    try:
                        last_date = datetime.strptime(days[-1], "%Y-%m-%d").date()
                    except ValueError:
                        last_date = None
            stale = (last_date is None or
                     last_date <= (_now() - timedelta(hours=AUTO_DREAM_HOURS)).date())
            if not (pending >= AUTO_DREAM_SIGNALS or stale):
                return False
            memo, _ = self.dreamer.dream(dry_run=False)
            self.active.generate(self.root)
            self.active.export_to_agents(self.root)
            print("  🌙 auto-dream: memory consolidated (%s)"
                  % (memo or "journal skipped"))
            return True
        except Exception as e:                       # noqa: BLE001
            # maintenance must never break the write it rode on
            print("  (auto-dream skipped: %s)" % e, file=sys.stderr)
            return False

    def remember(self, text):
        self._ensure()
        nid = self.hippo.remember(text)
        self.active.generate(self.root)
        self.active.export_to_agents(self.root)
        print("remembered: %s" % self.hippo.nodes[nid]["text"])
        print("  (node %s, total nodes: %d)" % (nid, len(self.hippo.nodes)))
        print("  ACTIVE.md + AGENTS.md/CLAUDE.md/GEMINI.md updated")
        self._auto_dream()

    def link(self, a, b, relation="related"):
        self._ensure()
        print(self.hippo.link(a, b, relation))
        self._auto_dream()

    def recall(self, query, at=None):
        self._ensure()
        results, latency, kinds = self.hippo.recall(query, at=at)
        if not results:
            print("no results for \"%s\"%s (empty graph or no match)"
                  % (query, " at %s" % at[:10] if at else ""))
            return
        when = " (as of %s)" % at[:10] if at else ""
        print("recall for \"%s\"%s — %d results [%.2f ms]\n"
              % (query, when, len(results), latency))
        for i, (nid, score, n) in enumerate(results, 1):
            print("  %d. [%.3f] (%s) %s" % (i, score, kinds.get(nid, "trace"), n["text"]))
            print("     (confidence %.1f, recalled %dx, weight %.2f, id %s)"
                  % (n.get("confidence", 1), n.get("access_count", 0),
                     n["weight"], nid))
        print("\n  (if a result actually answered you, reinforce it:"
              " python3 mind.py confirm <id>)")

    def confirm(self, node_ids):
        self._ensure()
        known = [nid for nid in node_ids if nid in self.hippo.nodes]
        unknown = [nid for nid in node_ids if nid not in self.hippo.nodes]
        if known:
            self.hippo.bump(known)
            print("reinforced %d memor%s — stability +%d days each, edges "
                  "restrengthened" % (len(known), "y" if len(known) == 1 else "ies",
                                      int(STABILITY_PER_ACCESS)))
            self._auto_dream()
        for nid in unknown:
            print("unknown id: %s (get ids from `recall` output)" % nid,
                  file=sys.stderr)
        if not known:
            sys.exit(1)

    def correct(self, old_hint, new_text):
        self._ensure()
        old = self.hippo.correct(old_hint, new_text)
        if old is None:
            print("no memory matched \"%s\" — nothing corrected." % old_hint)
            return
        if self.hippo._clean_text(new_text) == old:
            print("already current — nothing changed.")
            return
        self.active.generate(self.root)
        self.active.export_to_agents(self.root)
        print("reconsolidated:")
        print("  was: %s" % old)
        print("  now: %s" % self.hippo._clean_text(new_text))
        print("  (old fact CLOSED, not erased — `why` and `--at` can still reach it)")
        self._auto_dream()

    def why(self, nid):
        """Full provenance answer for one memory: where did this fact
        come from, is it still true, and every event in its life."""
        self._ensure()
        n = self.hippo.nodes.get(nid)
        if n is None:
            # the fact may have been pruned from the graph — the journal
            # is permanent, so provenance must still answer (auditor
            # finding: the docs promised lineage the command refused)
            events = self.hippo.journal_entries(nid)
            if not events:
                print("unknown id: %s (get ids from `recall` or `entity`)"
                      % nid, file=sys.stderr)
                sys.exit(1)
            print("memory %s" % nid)
            print("  status:     PRUNED from the graph — journal lineage:")
            for e in events:
                extra = ""
                for f in ("text", "old_text", "new_text"):
                    if e.get(f):
                        extra = "  %s" % e[f][:70]
                        break
                print("    %s %s by=%s%s" % (e.get("ts", "?")[:19],
                                             e.get("op"),
                                             e.get("by", "?"), extra))
            return
        origin = n.get("origin", {})
        vt = n.get("valid_to")
        print("memory %s" % nid)
        print("  text:       %s" % n["text"])
        print("  status:     %s" % (
            "STILL TRUE (valid since %s)" % n.get("valid_from", "?")[:19]
            if vt is None else
            "SUPERSEDED on %s -> %s" % (vt[:19], n.get("superseded_by", "?"))))
        print("  origin:     by=%s via=%s%s" % (
            origin.get("by", "unknown"), origin.get("via", "unknown"),
            " session=%s" % origin["session"] if origin.get("session") else ""))
        print("  created:    %s" % n.get("created", "?")[:19])
        print("  confirmed:  %dx (confidence %.2f, weight %.2f)"
              % (n.get("access_count", 0), n.get("confidence", 1.0),
                 n.get("weight", 1.0)))
        for h in n.get("history", []):
            print("  previously: %s (replaced %s)"
                  % (h.get("text", "?"), h.get("replaced", "?")[:19]))
        rels = [(nbr, e) for nbr, e in self.hippo.edges.get(nid, {}).items()
                if e.get("relation") in ("supersedes", "superseded-by")]
        for nbr, e in rels:
            other = self.hippo.nodes.get(nbr, {})
            print("  %s: %s (%s)" % (e["relation"], nbr,
                                     other.get("text", "?")[:60]))
        events = self.hippo.journal_entries(nid)
        if events:
            trunc = ("" if len(events) <= 8 else
                     "; last 8 shown — full log in .mind/journal.jsonl")
            print("  journal (%d events%s):" % (len(events), trunc))
            for e in events[-8:]:
                print("    %s %s%s" % (e.get("ts", "?")[:19], e.get("op"),
                                       " by=%s" % e.get("by") if e.get("by")
                                       else ""))
        else:
            print("  journal:    (no entries — predates 6.0.0 or journal lost)")

    def entity(self, term):
        """Entity view: every fact — current and superseded — that
        mentions this (normalized) term, with validity intervals."""
        self._ensure()
        term_l = term.lower()
        # multi-word NORMALIZE phrases ("تايب سكريبت") must be replaced
        # before tokenization, exactly as _extract_keys does (auditor
        # finding: entity missed them while recall found them)
        for phrase, rep in _NORMALIZE_PHRASES.items():
            if phrase in term_l:
                term_l = term_l.replace(phrase, " %s " % rep)
        toks = _tokenize(term_l)
        wanted = {NORMALIZE.get(t, t) for t in toks} | {stem(t) for t in toks}
        wanted.discard("")
        if not wanted:
            print("no indexable term in \"%s\"" % term)
            return
        rows = []
        for nid, n in self.hippo.nodes.items():
            nkeys = set(n.get("keys", []))
            nstems = {stem(k) for k in nkeys}
            if wanted & nkeys or wanted & nstems:
                rows.append((n.get("valid_from", ""), nid, n))
        if not rows:
            print("no facts mention \"%s\"" % term)
            return
        rows.sort()
        print("entity \"%s\" — %d fact(s):\n" % (term, len(rows)))
        for _, nid, n in rows:
            vt = n.get("valid_to")
            span = ("%s -> now" % n.get("valid_from", "?")[:10] if vt is None
                    else "%s -> %s" % (n.get("valid_from", "?")[:10], vt[:10]))
            mark = "  " if vt is None else "✗ "
            origin = n.get("origin", {})
            arrow = (" -> superseded by %s" % n["superseded_by"]
                     if n.get("superseded_by") else "")
            print("  %s[%s] %s (id %s, by %s via %s)%s"
                  % (mark, span, n["text"], nid,
                     origin.get("by", "unknown"),
                     origin.get("via", "?"), arrow))

    def dream(self, dry_run=False):
        self._ensure()
        memo, text = self.dreamer.dream(dry_run=dry_run)
        if dry_run:
            print(text)
            print("(dry run — nothing was written)")
            return
        self.active.generate(self.root)
        self.active.export_to_agents(self.root)
        print("dream cycle complete. journal: %s" % memo)
        print("  (read it to see what was forgotten, promoted, or flagged)")

    def export(self):
        self._ensure()
        self.active.generate(self.root)
        written = self.active.export_to_agents(self.root)
        print("exported memory to: %s" % ", ".join(written))

    def status(self):
        self._ensure()
        n_nodes = len(self.hippo.nodes)
        n_valid = sum(1 for n in self.hippo.nodes.values()
                      if self.hippo._valid_at(n))
        n_edges = len({frozenset((a, b))
                       for a, nbrs in self.hippo.edges.items()
                       for b in nbrs})
        avg_w = (sum(n["weight"] for n in self.hippo.nodes.values()
                     if self.hippo._valid_at(n)) / n_valid) if n_valid else 0
        cortex_n = len(list(self.cortex.files()))
        active_size = (self.dir / ACTIVE_FILE).stat().st_size if (self.dir / ACTIVE_FILE).exists() else 0
        pending = 0
        sig = self.dir / SIGNALS_FILE
        if sig.exists() and not sig.is_symlink() \
                and sig.stat().st_size <= 5_000_000:
            pending = sum(1 for ln in sig.read_text("utf-8").splitlines() if ln.strip())
        journal_n = len(self.hippo.journal_entries())
        print("""=== mind memory health ===
path:            %s
nodes:           %d (%d currently true, %d superseded)
edges:           %d
avg weight:      %.3f
cortex files:    %d
working memory:  %d bytes (~%d tokens)
pending signals: %d
journal events:  %d (append-only provenance)
version:         %s""" % (self.dir, n_nodes, n_valid, n_nodes - n_valid,
                          n_edges, avg_w, cortex_n,
                          active_size, active_size // 4, pending,
                          journal_n, __version__))


USAGE = """mind — brain-like memory for any coding agent (v%s)

usage: python3 mind.py <command> [args]

commands:
  init                    create .mind/ memory in this project
  remember "text"         add a memory
  link "a" "b" [rel]      connect two memories
  recall "question"       spreading-activation recall (prints memory ids)
  recall "q" --at DATE    what was true then (bare date = end of that day)
  confirm <id> [...]      reinforce memories that actually answered you
  correct "old" "new"     supersede a wrong fact (transition kept in graph)
  why <id>                provenance: where a fact came from, is it still true
  entity "term"           every fact about a term, current and superseded
  dream [--dry-run]       force a sleep cycle (also fires AUTOMATICALLY
                          after writes when >=%d signals pend or the last
                          dream is from a previous day; MIND_AUTO_DREAM=0
                          disables)
  export                  regenerate agent files
  status                  health report
""" % (__version__, AUTO_DREAM_SIGNALS)


def _die(msg, code=2):
    print("error: %s" % msg, file=sys.stderr)
    sys.exit(code)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("-v", "--version", "version"):
        print(__version__)
        return 0
    import difflib
    cmd = argv[0]
    COMMANDS = {"init", "remember", "link", "recall", "confirm", "correct",
                "why", "entity", "dream", "export", "status"}
    if cmd not in COMMANDS:
        sug = difflib.get_close_matches(cmd, COMMANDS, n=1, cutoff=0.6)
        hint = " did you mean `%s`?" % sug[0] if sug else ""
        _die("unknown command: %s.%s\n\n%s" % (cmd, hint, USAGE))
    # reject unknown flags: a typo like `dream --dryrun` must never fall
    # through to the destructive default
    KNOWN_FLAGS = {"dream": {"--dry-run"}, "recall": {"--at"}}
    if cmd in KNOWN_FLAGS:
        # strict scan ONLY for commands with flags: a typo like `dream
        # --dryrun` must never fall through to the destructive default —
        # but free-text commands must accept text that merely starts
        # with dashes (auditor finding)
        skip_value = False
        for a in argv[1:]:
            if skip_value:
                skip_value = False
                continue
            if a == "--at":
                skip_value = True
            if a.startswith("--") and a not in KNOWN_FLAGS[cmd]:
                _die("unknown option %s for `%s` (allowed: %s)" % (
                    a, cmd, ", ".join(sorted(KNOWN_FLAGS[cmd]))))
    m = Mind()
    try:
        if cmd == "init":
            m.init()
        elif cmd == "remember":
            text = " ".join(argv[1:]).strip()
            if not text:
                _die('usage: python3 mind.py remember "text" (text must not be empty)')
            m.remember(text)
        elif cmd == "link":
            if len(argv) < 3:
                _die('usage: python3 mind.py link "a" "b" ["relation"]')
            m.link(argv[1], argv[2], argv[3] if len(argv) > 3 else "related")
        elif cmd == "recall":
            args = argv[1:]
            at = None
            if "--at" in args:
                i = args.index("--at")
                if i + 1 >= len(args):
                    _die("--at needs a date: recall \"q\" --at YYYY-MM-DD")
                at = args[i + 1]
                args = args[:i] + args[i + 2:]
                try:
                    datetime.fromisoformat(at)
                except ValueError:
                    _die("invalid --at date %r (use YYYY-MM-DD)" % at)
                if len(at) == 10:          # bare date → inclusive end of day
                    at += "T23:59:59"
            q = " ".join(args).strip()
            if not q:
                _die('usage: python3 mind.py recall "question" [--at YYYY-MM-DD]')
            m.recall(q, at=at)
        elif cmd == "why":
            if len(argv) != 2 or not argv[1].strip():
                _die('usage: python3 mind.py why <id> (ids come from recall/entity output)')
            m.why(argv[1].strip())
        elif cmd == "entity":
            term = " ".join(argv[1:]).strip()
            if not term:
                _die('usage: python3 mind.py entity "term"')
            m.entity(term)
        elif cmd == "confirm":
            if len(argv) < 2:
                _die('usage: python3 mind.py confirm <id> [<id>...] (ids come from recall output)')
            m.confirm(argv[1:])
        elif cmd == "correct":
            if len(argv) < 3 or not argv[1].strip() or not argv[2].strip():
                _die('usage: python3 mind.py correct "old text hint" "corrected fact" (neither may be empty)')
            m.correct(argv[1], argv[2])
        elif cmd == "dream":
            m.dream(dry_run="--dry-run" in argv[1:])
        elif cmd == "export":
            m.export()
        elif cmd == "status":
            m.status()
    except Exception as e:
        print("error: %s" % e, file=sys.stderr)
        if os.environ.get("MIND_DEBUG"):
            import traceback
            traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
