#!/usr/bin/env python3
"""
mind — brain-like memory for any coding agent.

Three layers (working / hippocampus / cortex) + spreading-activation recall
+ Ebbinghaus forgetting + dream consolidation between sessions + export to
common agent rule files (AGENTS.md / CLAUDE.md / GEMINI.md / tool-specific
dotfiles). One file. Zero dependencies.
Fully offline. Bilingual (English + Arabic) tokenization built in.

Usage: python3 mind.py <command> [args]
  init                 create .mind/ in the current project
  remember "text"      add a memory node to the graph
  link "a" "b" [rel]   connect two memories with a weighted edge
  recall "question"    spreading-activation recall (RRF + IDF fusion)
  correct "old" "new"  reconsolidate: rewrite a wrong memory, keep history
  dream [--dry-run]    run the sleep cycle (light -> deep -> REM)
  export               regenerate agent rule files
  status               memory health report

Design: docs/DESIGN.md  |  License: MIT  |  https://github.com/Da7-Tech/mind
"""
import sys, os, json, re, time, math, hashlib
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict

__version__ = "5.0.0"

# ────────────────────────────────────────────────────────────────
# Tunables (see docs/DESIGN.md for the reasoning behind each value)
# ────────────────────────────────────────────────────────────────
MIND_DIR = ".mind"
GRAPH_FILE = "graph.json"
ACTIVE_FILE = "ACTIVE.md"
CORTEX_DIR = "cortex"
DREAMS_DIR = "dreams"
SIGNALS_FILE = "signals.jsonl"

BOOST_PER_ACCESS = 0.15     # weight boost on confirmed recall (bump)
WEIGHT_THRESHOLD = 0.1      # nodes below this are pruned during dreams
RECALL_RADIUS = 3           # spreading-activation hop limit (cheap, local)
RECALL_TOP_K = 5
ACTIVATION_DECAY = 0.5      # activation halves at each hop
SPREADING_THRESHOLD = 0.05  # do not propagate activation below this
PROMOTION_THRESHOLD = 3     # cluster of >= 3 related nodes -> cortex
ACTIVE_TOKEN_BUDGET = 800   # working-memory budget in characters (~200 tokens)
STABILITY_BASE_DAYS = 3.0   # Ebbinghaus: base memory stability
STABILITY_PER_ACCESS = 5.0  # each confirmed recall adds this many days
EDGE_PRUNE_THRESHOLD = 0.1  # edges below this are pruned during dreams
CLUSTER_SIM = 0.45          # similarity gate for dream clustering
SEPARATION_SIM = 0.92       # near-identical results are diversified in top-k
FUZZY_ACTIVATION = 0.5      # activation given to pattern-completion matches


def _now():
    return datetime.now()


def _atomic_write(path, data):
    """Atomic, symlink-safe write: O_NOFOLLOW + tmp + os.replace.

    Prevents TOCTOU symlink attacks and torn files on power loss."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"refusing to write through a symlink: {path}")
    tmp = str(path) + ".tmp"
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | nofollow | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data.encode("utf-8") if isinstance(data, str) else data)
    finally:
        os.close(fd)
    os.replace(tmp, str(path))


# ────────────────────────────────────────────────────────────────
# Bilingual tokenization + light stemming (English + Arabic)
# ────────────────────────────────────────────────────────────────
_TOKEN = re.compile(r"[\w؀-ۿ]{3,}", re.UNICODE)

STOPWORDS = frozenset({
    # English
    "the", "and", "for", "that", "with", "from", "this", "these", "those",
    "have", "has", "are", "was", "were", "not", "but", "you", "all", "can",
    "her", "him", "his", "she", "they", "them", "our", "out", "use", "using",
    "used", "what", "when", "where", "which", "who", "why", "how",
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

# Arabic identity pronouns: queries like "what is my name" / "من أنا" carry
# no content keys after stopword removal, so we fall back to identity keys.
PRONOUN_FALLBACK = {
    "انا", "أنا", "انني", "أني", "اسمي", "اسمنا", "مدينتي", "مدينتنا",
    "مشروعي", "مشروعنا", "عملي", "اعمل", "أعمل", "اين", "أين", "ماذا",
    "مشروعه", "تعمل", "تعملون", "تعملين",
    "name", "myself",
}
IDENTITY_KEYS = {"user", "project", "city", "name", "المستخدم", "المشروع",
                 "المدينة", "الاسم"}

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
}


def stem(w):
    """Light bilingual stemmer. Arabic: prefix/suffix stripping + broken-plural
    seed dictionary. English: common suffix stripping."""
    if w and "؀" <= w[0] <= "ۿ":  # Arabic
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
        for raw in _TOKEN.findall((text or "").lower()):
            if raw in STOPWORDS:
                continue
            t = stem(raw)
            if len(t) < 3 or t in STOPWORDS or t in seen:
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
        toks = [t for t in _TOKEN.findall((text or "").lower())
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
        self.related = None
        self.embedder = HashEmbed()
        self._deleted = set()   # node ids deleted this session (see _save merge)
        self._load()

    # -- persistence -------------------------------------------------
    def _quarantine(self, reason):
        """Never silently erase a user's memory: quarantine and start fresh."""
        bak = self.path.with_suffix(
            ".json.corrupt-%s" % _now().strftime("%H%M%S"))
        try:
            self.path.rename(bak)
            print("warning: could not read %s (%s).\n"
                  "  corrupt copy saved as %s; starting with empty memory."
                  % (self.path.name, reason, bak.name), file=sys.stderr)
        except OSError:
            pass
        return {}

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("graph.json is not a JSON object")
            if not isinstance(data.get("nodes", {}), dict) or \
                    not isinstance(data.get("edges", {}), dict):
                raise ValueError("nodes/edges have the wrong structure")
        except (json.JSONDecodeError, ValueError) as e:
            data = self._quarantine(e)
        self.nodes = data.get("nodes", {})
        self.edges = {k: v for k, v in data.get("edges", {}).items()
                      if isinstance(v, dict)}
        for nid, n in list(self.nodes.items()):
            if not isinstance(n, dict) or not isinstance(n.get("text", ""), str):
                del self.nodes[nid]
                continue
            n.setdefault("text", "")
            n.setdefault("weight", 1.0)
            n.setdefault("peak_weight", n.get("weight", 1.0))
            n.setdefault("confidence", 1.0)
            n.setdefault("access_count", 0)
            n.setdefault("keys", [])
            n.setdefault("last_accessed", _now().isoformat())
            n.setdefault("created", _now().isoformat())
            n["keys"] = [re.sub(r'[،؛؟!."\']', '', k).strip()
                         for k in n.get("keys", [])]
            n["keys"] = [k for k in n["keys"] if k]

    def _save(self):
        """Locked read-merge-write: concurrent agent processes cannot lose
        each other's writes. Inside the lock we re-read the graph from disk
        and merge nodes/edges written by other processes since our load
        (our changes win per node; our deletions stay deleted)."""
        lock_path = self.path.with_suffix(".json.lock")
        try:
            lock_path.touch(exist_ok=True)
        except OSError:
            pass
        with open(lock_path, "w") as lockf:
            try:
                import fcntl
                fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
            except ImportError:            # Windows: fall back to atomic write only
                fcntl = None
            try:
                if self.path.exists():
                    try:
                        disk = json.loads(self.path.read_text("utf-8"))
                    except (json.JSONDecodeError, ValueError):
                        disk = {}
                    dn = disk.get("nodes", {}) if isinstance(disk, dict) else {}
                    de = disk.get("edges", {}) if isinstance(disk, dict) else {}
                    if isinstance(dn, dict) and isinstance(de, dict):
                        merged_n = {k: v for k, v in dn.items()
                                    if k not in self._deleted and isinstance(v, dict)}
                        merged_n.update(self.nodes)
                        merged_e = {k: v for k, v in de.items()
                                    if k not in self._deleted and isinstance(v, dict)}
                        merged_e.update(self.edges)
                        for nbrs in merged_e.values():
                            for d in self._deleted:
                                nbrs.pop(d, None)
                        self.nodes, self.edges = merged_n, merged_e
                _atomic_write(self.path, json.dumps(
                    {"nodes": self.nodes, "edges": self.edges},
                    ensure_ascii=False, indent=2))
            finally:
                if fcntl is not None:
                    fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _id(text):
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

    # -- key extraction ----------------------------------------------
    def _ensure_related(self):
        if self.related is None:
            corpus = [n.get("text", "") for n in self.nodes.values()]
            if corpus:
                self.related = RelatedTerms(corpus, min_df=1)

    def _extract_keys(self, text, is_query=False):
        """Three cooperating layers:
        1. NORMALIZE seed (cross-language term bridging)
        2. co-occurrence expansion (RelatedTerms, self-building)
        3. identity fallback — queries only ("what is my name" carries no
           content keys). Stored memories never get fallback identity keys:
           a content-free memory must not pollute identity recalls."""
        cleaned = re.sub(r'[،؛؟!.,"\']', ' ', text)
        words = re.findall(r'[\w؀-ۿ]{3,}', cleaned.lower())
        keys = set()
        for w in words:
            if w in STOPWORDS:
                continue
            keys.add(NORMALIZE.get(w, w))
        text_tokens = set(re.findall(r'[\w؀-ۿ]+', cleaned.lower()))
        if text_tokens & PRONOUN_FALLBACK or (is_query and len(keys) == 0):
            keys.update(IDENTITY_KEYS)
        self._ensure_related()
        if self.related is not None:
            for w in list(keys):
                for term, sc in self.related.related(w, top_k=4):
                    if sc >= 0.15:
                        keys.add(term)
        return list(keys)[:24]

    # -- write path ---------------------------------------------------
    def remember(self, text, confidence=1.0):
        if not text or not text.strip():
            raise ValueError("cannot remember empty text")
        # strip terminal control characters (keep newlines/tabs) so stored
        # text can never carry ANSI escapes back to a terminal on recall
        text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text).strip()
        if not text:
            raise ValueError("cannot remember control-characters-only text")
        nid = self._id(text)
        if nid in self.nodes:
            n = self.nodes[nid]
            n["weight"] = min(1.0, n["weight"] + 0.2)
            n["peak_weight"] = max(n.get("peak_weight", 1.0), n["weight"])
            n["access_count"] = n.get("access_count", 0) + 1
            n["last_accessed"] = _now().isoformat()
            n["confidence"] = max(n.get("confidence", 1.0), confidence)
        else:
            self.nodes[nid] = {
                "text": text,
                "weight": 1.0,
                "peak_weight": 1.0,
                "created": _now().isoformat(),
                "last_accessed": _now().isoformat(),
                "access_count": 0,
                "confidence": confidence,
                "keys": self._extract_keys(text),
            }
            self.edges.setdefault(nid, {})
            self.related = None    # rebuild lazily; avoids O(N^2) per write
        self._save()
        self._log_signal("remember", text)
        return nid

    def link(self, text_a, text_b, relation="related"):
        id_a, id_b = self._id(text_a.strip()), self._id(text_b.strip())
        if id_a not in self.nodes:
            self.remember(text_a)
        if id_b not in self.nodes:
            self.remember(text_b)
        self.edges.setdefault(id_a, {})[id_b] = {"relation": relation, "weight": 1.0}
        self.edges.setdefault(id_b, {})[id_a] = {"relation": relation, "weight": 1.0}
        self._save()
        self._log_signal("link", "%s --%s--> %s" % (text_a, relation, text_b))
        return "linked: %s <-> %s" % (text_a, text_b)

    @staticmethod
    def _content_tokens(text):
        """Raw stemmed content tokens — no expansion, no identity fallback.
        Used to gate destructive operations on real lexical overlap."""
        return {stem(w) for w in _TOKEN.findall((text or "").lower())
                if w not in STOPWORDS}

    def correct(self, old_hint, new_text):
        """Reconsolidation: find the memory best matching `old_hint`,
        rewrite it with `new_text`, keep the old text in node history,
        and temporarily lower confidence (it re-hardens on future recalls).

        Destructive-op gate: the match must share at least two content
        tokens with the hint (or cover half of a short hint) — a one-word
        coincidence must never rewrite an unrelated memory."""
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
        history = node.setdefault("history", [])
        history.append({"text": old_text, "replaced": _now().isoformat()})
        new_nid = self._id(new_text.strip())
        node["text"] = new_text.strip()
        node["keys"] = self._extract_keys(new_text)
        node["confidence"] = round(node.get("confidence", 1.0) * 0.7, 3)
        node["last_accessed"] = _now().isoformat()
        # re-key the node under its new id so remember(new_text) converges
        self.nodes[new_nid] = node
        if new_nid != nid:
            del self.nodes[nid]
            self._deleted.add(nid)
            self.edges[new_nid] = self.edges.pop(nid, {})
            for other in self.edges.values():
                if nid in other:
                    other[new_nid] = other.pop(nid)
        self.related = None
        self._save()
        self._log_signal("correct", "%s => %s" % (old_text, new_text))
        return old_text

    # -- read path ------------------------------------------------------
    def recall(self, query, top_k=RECALL_TOP_K, max_hops=RECALL_RADIUS,
               rrf_k=60):
        """Spreading activation + IDF + Reciprocal Rank Fusion.

        Read-only by design: recall never writes to disk. Reinforcement
        happens through the separate bump() (called on confirmed hits),
        so health checks and repeated queries cannot skew the weights."""
        t0 = time.perf_counter()
        keys = set(self._extract_keys(query, is_query=True))
        if not keys:
            return [], 0.0, {}
        expanded = set(keys)
        if self.related is not None:
            for w in list(keys):
                for term, _ in self.related.related(w, top_k=4):
                    expanded.add(term)
        keys = expanded
        N = max(1, len(self.nodes))

        df = defaultdict(int)
        for node in self.nodes.values():
            for k in set(node.get("keys", [])):
                df[k] += 1
        idf = {k: math.log(1 + N / (1 + df.get(k, 0))) for k in keys}

        # direct channel: IDF-weighted key overlap + substring containment
        direct = defaultdict(float)
        q_lower = query.lower()
        for nid, node in self.nodes.items():
            n_keys = set(node.get("keys", []))
            shared = keys & n_keys
            if shared:
                direct[nid] += sum(idf[k] for k in shared) * node.get("weight", 1.0)
            n_text = node["text"].lower()
            substr = sum(1 for w in keys if len(w) >= 4 and w in n_text)
            reverse = sum(1 for k in n_keys if len(k) >= 4 and k in q_lower)
            if substr + reverse:
                direct[nid] += (substr + reverse) * 0.6 * node.get("weight", 1.0)

        # pattern completion: no direct hits -> fuzzy-match node texts so a
        # partial or misspelled cue can still reactivate the memory.
        if not direct and self.nodes:
            for nid, node in self.nodes.items():
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

        # lexical-semantic re-rank of the head (offline hash embeddings)
        ranked = sorted(fused.items(), key=lambda x: -x[1])
        if len(ranked) > 1:
            reranked = []
            for nid, base in ranked[:top_k * 3]:
                sim = self.embedder.similarity(query, self.nodes[nid]["text"])
                reranked.append((nid, base + sim * 0.5))
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
        recall() so reads stay pure). Tracks peak_weight for Ebbinghaus."""
        now = _now()
        changed = False
        for nid in node_ids:
            if nid in self.nodes:
                n = self.nodes[nid]
                n["access_count"] = n.get("access_count", 0) + 1
                n["weight"] = min(1.0, n["weight"] + BOOST_PER_ACCESS)
                n["peak_weight"] = max(n.get("peak_weight", 1.0), n["weight"])
                n["last_accessed"] = now.isoformat()
                changed = True
        if changed:
            self._save()
        return changed

    def decay(self, dry_run=False):
        """Ebbinghaus forgetting curve: R = e^(-t/S).

        Stability S grows with each confirmed recall, so frequently used
        memories decay slowly while one-off trivia fades fast. Nodes that
        fall below WEIGHT_THRESHOLD with < 2 recalls are pruned."""
        now = _now()
        pruned = []
        for nid in list(self.nodes.keys()):
            n = self.nodes[nid]
            try:
                days = (now - datetime.fromisoformat(n["last_accessed"])).days
            except ValueError:
                days = 0
            access = n.get("access_count", 0)
            stability = STABILITY_BASE_DAYS + access * STABILITY_PER_ACCESS
            retention = math.exp(-days / stability)
            new_weight = max(0.0, n.get("peak_weight", 1.0) * retention)
            if not dry_run:
                n["weight"] = new_weight
            if new_weight < WEIGHT_THRESHOLD and access < 2:
                pruned.append(n["text"])
                if not dry_run:
                    del self.nodes[nid]
                    self._deleted.add(nid)
                    self.edges.pop(nid, None)
                    for other in self.edges.values():
                        other.pop(nid, None)
        if not dry_run:
            self._save()
        return pruned

    def _log_signal(self, kind, content):
        sig_file = self.path.parent / SIGNALS_FILE
        if sig_file.is_symlink():
            return
        with sig_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": kind, "content": content,
                                "ts": _now().isoformat()},
                               ensure_ascii=False) + "\n")


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
        header = "# %s\n\n> promoted by dream on %s\n\n" % (topic, _now().date())
        _atomic_write(fpath, header + content + "\n")
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
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hippo = hippo
        self.cortex = cortex
        self.signals_file = mind_dir / SIGNALS_FILE

    def dream(self, dry_run=False):
        mode = " (dry run — nothing written)" if dry_run else ""
        log = ["# Dream journal — %s%s" % (_now().date(), mode), ""]
        log.append("_cycle started %s_" % _now().strftime("%H:%M"))

        # 1. light sleep: ingest session signals
        signals = self._read_signals()
        log.append("\n## Light sleep\nIngested %d session signals." % len(signals))

        # 2. deep sleep: Ebbinghaus decay + node pruning
        pruned = self.hippo.decay(dry_run=dry_run)
        log.append("\n## Deep sleep")
        log.append("- decay: %s %d weak nodes."
                   % ("would prune" if dry_run else "pruned", len(pruned)))
        for t in pruned[:5]:
            log.append("  - forgot: %s" % t)
        if len(pruned) > 5:
            log.append("  - ... and %d more" % (len(pruned) - 5))

        # synaptic pruning: weak edges die, isolated stubs are removed
        pruned_edges = 0
        for nid in list(self.hippo.edges.keys()):
            for neighbor in list(self.hippo.edges[nid].keys()):
                if self.hippo.edges[nid][neighbor].get("weight", 1.0) < EDGE_PRUNE_THRESHOLD:
                    if not dry_run:
                        del self.hippo.edges[nid][neighbor]
                    pruned_edges += 1
            if not dry_run and nid in self.hippo.edges and not self.hippo.edges[nid]:
                del self.hippo.edges[nid]
        if pruned_edges:
            log.append("- synaptic pruning: %s %d weak edges."
                       % ("would remove" if dry_run else "removed", pruned_edges))

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
        _atomic_write(memo, memo_text)
        if self.signals_file.exists():
            self.signals_file.unlink()
        return str(memo.relative_to(self.hippo.path.parent)), memo_text

    def _rem_promote(self, log, dry_run):
        emb = self.hippo.embedder
        clusters = []
        for nid, n in self.hippo.nodes.items():
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
                    self.cortex.promote(topic, "\n".join("- %s" % t for t in texts))
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
        nodes = list(self.hippo.nodes.items())
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
                        self.hippo.edges.setdefault(ida, {})[idb] = {
                            "relation": "possible-conflict", "weight": 0.5}
                        self.hippo.edges.setdefault(idb, {})[ida] = {
                            "relation": "possible-conflict", "weight": 0.5}
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
        if not self.signals_file.exists():
            return []
        out = []
        for line in self.signals_file.read_text("utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


# ────────────────────────────────────────────────────────────────
# Layer 1: Working memory — always-on context + agent export
# ────────────────────────────────────────────────────────────────
class Active:
    BEGIN = "<!-- mind:memory begin (auto-generated, do not edit) -->"
    END = "<!-- mind:memory end -->"
    TARGETS = (
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        ".cursorrules",
        ".windsurfrules",
        ".clinerules",
        ".roo/rules/mind.md",
    )

    def __init__(self, mind_dir, hippo, cortex):
        self.dir = mind_dir
        self.hippo = hippo
        self.cortex = cortex
        self.path = mind_dir / ACTIVE_FILE

    def generate(self, project_root):
        nodes_sorted = sorted(self.hippo.nodes.values(),
                              key=lambda n: -n["weight"])
        hot, used = [], 0
        for n in nodes_sorted:
            line = "- %s" % n["text"]
            if used + len(line) > ACTIVE_TOKEN_BUDGET * 4:
                continue    # skip oversized memories, keep filling the budget
            hot.append(line)
            used += len(line)
            if len(hot) >= 8:
                break
        cortex_files = ["- `cortex/%s`" % f.name for f in self.cortex.files()[:6]]
        content = """# ACTIVE.md — mind working memory

> auto-generated %s — do not edit by hand (use `python3 mind.py ...`).

## How the agent uses this memory
- Need something not listed below? Run `python3 mind.py recall "your question"`.
- Learned something new? Run `python3 mind.py remember "the fact"`.
- Two facts belong together? `python3 mind.py link "a" "b" "relation"`.
- A stored fact is wrong? `python3 mind.py correct "old" "corrected fact"`.
- Between sessions: `python3 mind.py dream` reorganizes memory; journal in `.mind/dreams/`.

## Hot memories (highest weight now)
%s

## Cortex index (consolidated knowledge)
%s

## Agent behavior rules
- Do not guess. If the answer is not here, run `recall` before assuming.
- Record new durable facts with `remember` as you learn them.
- Every memory has a weight: recalled often -> reinforced; unused -> decays and is pruned.
""" % (_now().strftime("%Y-%m-%d %H:%M"),
            "\n".join(hot) if hot else "- (memory is empty — start with `remember`)",
            "\n".join(cortex_files) if cortex_files else "- (no cortex yet)")
        _atomic_write(self.path, content)
        return str(self.path.relative_to(project_root))

    def export_to_agents(self, project_root):
        """Write the working-memory block into every agent's instruction file,
        preserving any user content outside the guard markers."""
        src = self.path.read_text("utf-8") if self.path.exists() else ""
        written = []
        for target in self.TARGETS:
            target_path = Path(target)
            tpath = project_root / target_path
            parent = project_root
            skip = False
            for part in target_path.parent.parts:
                parent = parent / part
                if parent.exists() and parent.is_symlink():
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
                if self.BEGIN in content:
                    before = content.split(self.BEGIN)[0]
                    after = content.split(self.END)[-1] if self.END in content else ""
                    user_content = (before + after).strip()
                    # strip our own separator artifacts so re-export is idempotent
                    user_content = re.sub(
                        r'^---\s*\n<!-- user content below -->\s*\n?', '',
                        user_content).strip()
                else:
                    stripped = content.strip()
                    # Do not re-ingest our own stale block as "user content"
                    if stripped.startswith("# ACTIVE.md") or "mind working memory" in stripped:
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

agent files exported:
  AGENTS.md   (Codex, Cursor, Zed, ...)
  CLAUDE.md   (Claude Code)
  GEMINI.md   (Gemini CLI)
  .cursorrules / .windsurfrules / .clinerules / .roo/rules/mind.md

start with:  python3 mind.py remember "first thing to remember"
then:        python3 mind.py recall "your question"
between sessions:  python3 mind.py dream""" % self.dir)

    def _ensure(self):
        if not self.dir.exists():
            print("no mind memory here. run: python3 mind.py init", file=sys.stderr)
            sys.exit(1)
        self.hippo = Hippocampus(self.dir / GRAPH_FILE)
        self.cortex = Cortex(self.dir / CORTEX_DIR)
        self.dreamer = Dreamer(self.dir, self.hippo, self.cortex)
        self.active = Active(self.dir, self.hippo, self.cortex)

    def remember(self, text):
        self._ensure()
        nid = self.hippo.remember(text)
        self.active.generate(self.root)
        self.active.export_to_agents(self.root)
        print("remembered: %s" % text)
        print("  (node %s, total nodes: %d)" % (nid, len(self.hippo.nodes)))
        print("  ACTIVE.md + AGENTS.md/CLAUDE.md/GEMINI.md updated")

    def link(self, a, b, relation="related"):
        self._ensure()
        print(self.hippo.link(a, b, relation))

    def recall(self, query):
        self._ensure()
        results, latency, kinds = self.hippo.recall(query)
        if not results:
            print("no results for \"%s\" (empty graph or no match)" % query)
            return
        print("recall for \"%s\" — %d results [%.2f ms]\n" % (query, len(results), latency))
        for i, (nid, score, n) in enumerate(results, 1):
            print("  %d. [%.3f] (%s) %s" % (i, score, kinds.get(nid, "trace"), n["text"]))
            print("     (confidence %.1f, recalled %dx, weight %.2f)"
                  % (n.get("confidence", 1), n.get("access_count", 0), n["weight"]))

    def correct(self, old_hint, new_text):
        self._ensure()
        old = self.hippo.correct(old_hint, new_text)
        if old is None:
            print("no memory matched \"%s\" — nothing corrected." % old_hint)
            return
        self.active.generate(self.root)
        self.active.export_to_agents(self.root)
        print("reconsolidated:")
        print("  was: %s" % old)
        print("  now: %s" % new_text)
        print("  (old text kept in node history; confidence lowered until re-confirmed)")

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
        n_edges = sum(len(v) for v in self.hippo.edges.values()) // 2
        avg_w = (sum(n["weight"] for n in self.hippo.nodes.values()) / n_nodes) if n_nodes else 0
        cortex_n = len(list(self.cortex.files()))
        active_size = (self.dir / ACTIVE_FILE).stat().st_size if (self.dir / ACTIVE_FILE).exists() else 0
        pending = 0
        sig = self.dir / SIGNALS_FILE
        if sig.exists():
            pending = sum(1 for ln in sig.read_text("utf-8").splitlines() if ln.strip())
        print("""=== mind memory health ===
path:            %s
nodes:           %d
edges:           %d
avg weight:      %.3f
cortex files:    %d
working memory:  %d bytes (~%d tokens)
pending signals: %d
version:         %s""" % (self.dir, n_nodes, n_edges, avg_w, cortex_n,
                          active_size, active_size // 4, pending, __version__))


USAGE = """mind — brain-like memory for any coding agent (v%s)

usage: python3 mind.py <command> [args]

commands:
  init                    create .mind/ memory in this project
  remember "text"         add a memory
  link "a" "b" [rel]      connect two memories
  recall "question"       spreading-activation recall
  correct "old" "new"     fix a wrong memory (reconsolidation)
  dream [--dry-run]       run the sleep cycle
  export                  regenerate agent files
  status                  health report
""" % __version__


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
    COMMANDS = {"init", "remember", "link", "recall", "correct", "dream",
                "export", "status"}
    if cmd not in COMMANDS:
        sug = difflib.get_close_matches(cmd, COMMANDS, n=1, cutoff=0.6)
        hint = " did you mean `%s`?" % sug[0] if sug else ""
        _die("unknown command: %s.%s\n\n%s" % (cmd, hint, USAGE))
    # reject unknown flags: a typo like `dream --dryrun` must never fall
    # through to the destructive default
    KNOWN_FLAGS = {"dream": {"--dry-run"}}
    for a in argv[1:]:
        if a.startswith("--") and a not in KNOWN_FLAGS.get(cmd, set()):
            allowed = KNOWN_FLAGS.get(cmd)
            _die("unknown option %s for `%s`%s" % (
                a, cmd,
                " (allowed: %s)" % ", ".join(sorted(allowed)) if allowed else ""))
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
            q = " ".join(argv[1:]).strip()
            if not q:
                _die('usage: python3 mind.py recall "question"')
            m.recall(q)
        elif cmd == "correct":
            if len(argv) < 3:
                _die('usage: python3 mind.py correct "old text hint" "corrected fact"')
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
