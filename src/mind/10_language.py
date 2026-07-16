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
    MAX_CACHE_BYTES = 16_000_000

    def __init__(self, dim=512):
        self.dim = dim
        self._cache = {}
        self._cache_bytes = 0

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
        vector_bytes = len(v) * 32
        if (len(self._cache) < 4096 and
                self._cache_bytes + vector_bytes <= self.MAX_CACHE_BYTES):
            self._cache[text] = v
            self._cache_bytes += vector_bytes
        return v

    def similarity(self, a, b):
        va, vb = self.embed(a), self.embed(b)
        dot = sum(x * y for x, y in zip(va, vb))
        na = math.sqrt(sum(x * x for x in va)) or 1.0
        nb = math.sqrt(sum(y * y for y in vb)) or 1.0
        return max(0.0, dot / (na * nb))
