# Layer 2: Hippocampus — the weighted concept graph
# ────────────────────────────────────────────────────────────────
class Hippocampus:
    """Light graph: nodes (memories) + weighted edges (relations).
    Recall = local spreading activation (<= RECALL_RADIUS hops), fused
    with direct keyword matches via Reciprocal Rank Fusion + IDF."""

    def __init__(self, path):
        self.path = Path(path)
        self.nodes = {}   # id -> {text, weight, peak_weight, last_accessed,
        #                          access_count, created, confidence, keys, history}
        self.edges = {}   # id -> {neighbor_id: {relation, weight}}
        self.meta = {}    # small persisted strings (e.g. last_edge_decay)
        self.related = None
        self.embedder = HashEmbed()
        self.reranker = CommandEmbed(
            fallback=self.embedder, project_root=self.path.parent.parent)
        self.last_recall_explain = {}
        self._thread_lock = threading.RLock()
        self._transaction_state = threading.local()
        self._load()

    # -- persistence -------------------------------------------------
    def _quarantine(self, reason):
        """Never silently erase a user's memory: quarantine and start fresh."""
        bak = self.path.with_suffix(
            ".json.corrupt-%s-%d" % (_now().strftime("%H%M%S%f"),
                                     os.getpid()))
        try:
            if os.name != "nt" and os.rename in getattr(
                    os, "supports_dir_fd", set()):
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | \
                    getattr(os, "O_NOFOLLOW", 0)
                parent_fd = os.open(str(self.path.parent), flags)
                try:
                    info = os.stat(
                        self.path.name, dir_fd=parent_fd,
                        follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise UnsafePathError(
                            "refusing to quarantine an unsafe graph")
                    os.rename(
                        self.path.name, bak.name,
                        src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            else:
                if self.path.is_symlink() or not self.path.is_file():
                    raise UnsafePathError(
                        "refusing to quarantine an unsafe graph")
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
        except (TypeError, ValueError, OverflowError):
            return default
        if not math.isfinite(f):
            return default
        if lo is not None:
            f = max(lo, f)
        if hi is not None:
            f = min(hi, f)
        return f

    @staticmethod
    def _iso_timestamp(value, default):
        """Return a naive-local ISO timestamp or `default` when malformed."""
        if not isinstance(value, str):
            return default
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return default
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed.isoformat()

    @classmethod
    def _metadata_text(cls, value, default, cap):
        if not isinstance(value, str):
            return default
        cleaned = " ".join(cls._clean_text(value).split())[:cap]
        return cleaned or default

    def _repair_nodes(self, raw):
        """Repair a nodes dict fresh off the disk — shared by _load AND the
        _save read-merge-write. The merge used to import raw disk nodes
        past all of _load's repair, so a corrupt file left by a hand-edit
        or another (buggier) process re-poisoned a healthy session's graph
        on its next save (fuzzer finding)."""
        out = {}
        now_iso = _now().isoformat()
        for nid, n in list(raw.items())[:MAX_NODES]:
            if not isinstance(n, dict) or not isinstance(n.get("text", ""), str):
                continue
            n["text"] = self._clean_text(n.get("text", ""))
            if not n["text"]:
                continue
            n.setdefault("text", "")
            n.setdefault("weight", 1.0)
            n.setdefault("peak_weight", n.get("weight", 1.0))
            n.setdefault("confidence", 1.0)
            n.setdefault("access_count", 0)
            n.setdefault("keys", [])
            n.setdefault("last_accessed", now_iso)
            n.setdefault("created", now_iso)
            # timestamps must be ISO strings: a hand-edit that leaves a
            # number here would crash decay with TypeError, not ValueError
            # (auditor finding) — repair to "now" like the numeric fields
            for f in ("last_accessed", "created"):
                n[f] = self._iso_timestamp(n.get(f), now_iso)
            # numeric fields: repair non-numeric AND non-finite values, and
            # clamp to the range every write path maintains (auditor +
            # fuzzer findings)
            n["weight"] = self._finite(n["weight"], 1.0, 0.0, 1.0)
            n["peak_weight"] = self._finite(n["peak_weight"], 1.0, 0.0, 1.0)
            n["confidence"] = self._finite(n["confidence"], 1.0, 0.0, 1.0)
            n["access_count"] = int(self._finite(n["access_count"], 0, 0))
            # history must be a list — correct() appends to it (fuzzer
            # finding: a scalar history crashed reconsolidation)
            raw_history = n.get("history", [])
            if not isinstance(raw_history, list):
                raw_history = []
            history = []
            for h in raw_history:
                if not isinstance(h, dict):
                    continue
                raw_old_text = h.get("text", "")
                if not isinstance(raw_old_text, str):
                    continue
                old_text = self._clean_text(raw_old_text)
                if not old_text:
                    continue
                history.append({
                    "text": old_text[:MAX_TEXT_CHARS],
                    "replaced": self._iso_timestamp(
                        h.get("replaced"), "unknown"),
                })
                if len(history) >= MAX_HISTORY_PER_NODE:
                    break
            n["history"] = history
            # provenance + validity (6.0.0): older graphs get honest
            # defaults — origin "unknown", validity open since creation
            origin = n.get("origin")
            if not isinstance(origin, dict):
                origin = {}
            n["origin"] = {
                "by": self._metadata_text(origin.get("by"), "unknown", 80),
                "session": self._metadata_text(
                    origin.get("session"), None, 120),
                "via": self._metadata_text(origin.get("via"), "unknown", 40),
            }
            n["valid_from"] = self._iso_timestamp(
                n.get("valid_from"), n["created"])
            n["valid_to"] = self._iso_timestamp(n.get("valid_to"), None)
            memory_type = n.get("type", "semantic")
            n["type"] = (
                memory_type if memory_type in MEMORY_TYPES
                else "semantic")
            scope = n.get("scope", "project")
            n["scope"] = (
                scope if scope in MEMORY_SCOPES else "project")
            source_trust = n.get("source_trust", "user")
            n["source_trust"] = (
                source_trust if source_trust in MEMORY_TRUST else "user")
            sensitivity = n.get("sensitivity", "internal")
            n["sensitivity"] = (
                sensitivity if sensitivity in MEMORY_SENSITIVITY
                else "internal")
            n["authority"] = self._metadata_text(
                n.get("authority"), n["origin"]["by"], 80)
            n["expires_at"] = self._iso_timestamp(
                n.get("expires_at"), None)
            n["pinned"] = bool(n.get("pinned", False))
            for field in ("entity", "attr"):
                value = n.get(field)
                n[field] = (
                    self._metadata_text(value, None, 120)
                    if value is not None else None)
            if not (n.get("superseded_by") is None or
                    isinstance(n.get("superseded_by"), str)):
                n.pop("superseded_by", None)
            # keys must be a list of strings; a bare string would iterate
            # character-by-character and a non-string element would crash
            # the re.sub below (auditor finding)
            raw_keys = n.get("keys", [])
            if not isinstance(raw_keys, list):
                raw_keys = []
            n["keys"] = [re.sub(r'[،؛؟!."\']', '', self._clean_text(k))
                         .strip()[:100]
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
        edge_count = 0
        for nid, nbrs in raw.items():
            if nid not in nodes or not isinstance(nbrs, dict):
                continue
            clean = {}
            for nbr, e in nbrs.items():
                if edge_count >= MAX_EDGES:
                    break
                if nbr not in nodes or not isinstance(e, dict):
                    continue
                e["weight"] = self._finite(e.get("weight", 1.0), 1.0, 0.0, 1.0)
                e["relation"] = self._metadata_text(
                    e.get("relation"), "related", 60)
                e["directed"] = bool(e.get("directed", False))
                if e["directed"]:
                    e["inverse_relation"] = self._metadata_text(
                        e.get("inverse_relation"), "related", 60)
                else:
                    e.pop("inverse_relation", None)
                if e["relation"] == "possible-conflict":
                    kind = e.get("conflict_kind", "legacy")
                    e["conflict_kind"] = (
                        kind if kind in ("slot", "lexical", "legacy")
                        else "legacy")
                    if e["conflict_kind"] == "slot":
                        e["conflict_entity"] = self._metadata_text(
                            e.get("conflict_entity"), None, 120)
                        e["conflict_attr"] = self._metadata_text(
                            e.get("conflict_attr"), None, 120)
                    else:
                        e.pop("conflict_entity", None)
                        e.pop("conflict_attr", None)
                else:
                    e.pop("conflict_kind", None)
                    e.pop("conflict_entity", None)
                    e.pop("conflict_attr", None)
                if "created" in e:
                    created = self._iso_timestamp(e.get("created"), None)
                    if created is None:
                        e.pop("created", None)
                    else:
                        e["created"] = created
                clean[nbr] = e
                edge_count += 1
            if clean:
                out[nid] = clean
        return out

    def _load(self):
        if not self.path.exists():
            self.nodes = {}
            self.edges = {}
            self.meta = {}
            return
        try:
            data = json.loads(_read_text_retry(
                self.path, max_bytes=MAX_GRAPH_BYTES,
                boundary=self.path.parent))
            if not isinstance(data, dict):
                raise ValueError("graph.json is not a JSON object")
            if not isinstance(data.get("nodes", {}), dict) or \
                    not isinstance(data.get("edges", {}), dict):
                raise ValueError("nodes/edges have the wrong structure")
            if len(data.get("nodes", {})) > MAX_NODES:
                raise FileLimitError(
                    "graph exceeds %d nodes" % MAX_NODES)
            raw_edge_count = 0
            for raw_neighbors in data.get("edges", {}).values():
                if isinstance(raw_neighbors, dict):
                    raw_edge_count += len(raw_neighbors)
                    if raw_edge_count > MAX_EDGES:
                        raise FileLimitError(
                            "graph exceeds %d directional edges"
                            % MAX_EDGES)
        except UnsafePathError:
            raise
        except (json.JSONDecodeError, UnicodeError, FileLimitError,
                ValueError, RecursionError) as e:
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

    @contextmanager
    def _graph_lock(self):
        """Hold the one graph lock understood by every mind release.

        The lock covers the fresh read, the semantic decision, and the single
        graph commit. A per-object RLock serializes threads sharing one
        Hippocampus; the file lock serializes processes and older releases.
        """
        lock_path = self.path.with_suffix(".json.lock")
        try:
            lock_fd = _open_regular(
                lock_path, os.O_RDWR | os.O_CREAT,
                boundary=self.path.parent)
        except OSError as e:
            raise ValueError("refusing unsafe graph lock %s: %s"
                             % (lock_path, e))
        with os.fdopen(lock_fd, "r+b", buffering=0) as lockf:
            info = os.fstat(lockf.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("refusing unsafe graph lock: regular, "
                                 "single-link file required")
            if info.st_size == 0:
                lockf.write(b"\0")
                lockf.flush()
                os.fsync(lockf.fileno())
            lock_backend = None
            try:
                try:
                    import fcntl
                    timeout = self._finite(
                        os.environ.get("MIND_LOCK_TIMEOUT_SECONDS",
                                       LOCK_TIMEOUT_SECONDS),
                        LOCK_TIMEOUT_SECONDS, 0.1, 300.0)
                    deadline = time.monotonic() + timeout
                    while True:
                        try:
                            fcntl.flock(
                                lockf.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= deadline:
                                raise ValueError(
                                    "could not acquire the graph lock within "
                                    "%.1f seconds" % timeout)
                            time.sleep(0.05)
                    lock_backend = ("fcntl", fcntl)
                except ImportError:
                    try:
                        import msvcrt
                    except ImportError:
                        raise RuntimeError(
                            "no supported graph-lock backend is available")
                    else:
                        lockf.seek(0)
                        timeout = self._finite(
                            os.environ.get(
                                "MIND_LOCK_TIMEOUT_SECONDS",
                                LOCK_TIMEOUT_SECONDS),
                            LOCK_TIMEOUT_SECONDS, 0.1, 300.0)
                        nonblocking = getattr(msvcrt, "LK_NBLCK", None)
                        if nonblocking is not None:
                            deadline = time.monotonic() + timeout
                            while True:
                                try:
                                    msvcrt.locking(
                                        lockf.fileno(), nonblocking, 1)
                                    break
                                except OSError:
                                    if time.monotonic() >= deadline:
                                        raise ValueError(
                                            "could not acquire the graph "
                                            "lock within %.1f seconds"
                                            % timeout)
                                    time.sleep(0.05)
                        else:
                            # Older CRTs expose only the ~10-second blocking
                            # call. Bound the number of retries by the same
                            # configured timeout.
                            attempts = max(1, int(math.ceil(timeout / 10.0)))
                            for _attempt in range(attempts):
                                try:
                                    msvcrt.locking(
                                        lockf.fileno(), msvcrt.LK_LOCK, 1)
                                    break
                                except OSError:
                                    continue
                            else:
                                raise ValueError(
                                    "could not acquire the graph lock within "
                                    "%.1f seconds" % timeout)
                        lock_backend = ("msvcrt", msvcrt)
                yield
            finally:
                if lock_backend is not None:
                    name, module = lock_backend
                    if name == "fcntl":
                        module.flock(lockf.fileno(), module.LOCK_UN)
                    elif name == "msvcrt":
                        lockf.seek(0)
                        module.locking(lockf.fileno(), module.LK_UNLCK, 1)

    def _transaction_active(self):
        return bool(getattr(self._transaction_state, "depth", 0))

    @contextmanager
    def _transaction(self, preserve_local=False):
        """Run one semantic operation from a fresh snapshot to one commit."""
        with self._thread_lock:
            state = self._transaction_state
            if getattr(state, "depth", 0):
                state.depth += 1
                try:
                    yield
                finally:
                    state.depth -= 1
                return

            with self._graph_lock():
                state.depth = 1
                state.save_requested = False
                state.journal = []
                state.signals = []
                state.prunes = []
                try:
                    # Public operations always decide from the newest disk
                    # state while holding the graph lock. The only supported
                    # local-edit path is an explicit direct `_save()`, which
                    # opts into preserving the caller's in-memory graph.
                    if not preserve_local:
                        self._load()
                        self.related = None
                    self._recover_prune_outbox()
                    yield
                    self._flush_transaction()
                    self._journal_batch_immediate(state.journal)
                    self._log_signals_immediate(state.signals)
                except BaseException:
                    # Do not let a failed operation poison reuse of this
                    # object with an uncommitted in-memory graph.
                    try:
                        self._load()
                        self.related = None
                    except Exception:
                        pass
                    raise
                finally:
                    state.depth = 0
                    state.save_requested = False
                    state.journal = []
                    state.signals = []
                    state.prunes = []

    def _flush_transaction(self):
        """Commit queued graph work while retaining the outer graph lock."""
        state = self._transaction_state
        if not self._transaction_active() or not state.save_requested:
            return
        if state.prunes:
            self._stage_prunes(state.prunes)
            state.prunes = []
        self._commit_current()
        self._recover_prune_outbox()
        state.save_requested = False

    def _commit_current(self):
        """Persist the transaction's already-fresh graph exactly once."""
        if len(self.nodes) > MAX_NODES:
            raise FileLimitError("graph exceeds %d nodes" % MAX_NODES)
        edge_count = sum(len(nbrs) for nbrs in self.edges.values())
        if edge_count > MAX_EDGES:
            raise FileLimitError("graph exceeds %d directional edges"
                                 % MAX_EDGES)
        for node in self.nodes.values():
            history = node.get("history", [])
            if isinstance(history, list) and \
                    len(history) > MAX_HISTORY_PER_NODE:
                del history[:-MAX_HISTORY_PER_NODE]
        serialized = json.dumps(
            {
                "format": 2,
                "nodes": self.nodes,
                "edges": self.edges,
                "meta": self.meta,
            },
            ensure_ascii=False, indent=2)
        if len(serialized.encode("utf-8")) > MAX_GRAPH_BYTES:
            raise FileLimitError("graph exceeds the %d-byte limit"
                                 % MAX_GRAPH_BYTES)
        _atomic_write(self.path, serialized, boundary=self.path.parent)

    def _save(self):
        """Request the transaction's single graph commit.

        Direct callers are wrapped in the same fresh-read graph transaction;
        there is no second lock path and no atomic-only fallback.
        """
        if self._transaction_active():
            self._transaction_state.save_requested = True
            return
        with self._transaction(preserve_local=True):
            self._transaction_state.save_requested = True

    def decay_edges(self, dry_run=False):
        with self._transaction():
            return self._decay_edges(dry_run=dry_run)

    def _decay_edges(self, dry_run=False):
        """Apply synaptic homeostasis once per day, decided under the lock."""
        today = str(_now().date())
        if self.meta.get("last_edge_decay", "") >= today:
            return 0
        if dry_run:
            return sum(
                1 for nbrs in self.edges.values() for e in nbrs.values()
                if self._finite(e.get("weight", 1.0), 1.0, 0.0, 1.0)
                * EDGE_DECAY_PER_DREAM < EDGE_PRUNE_THRESHOLD)
        pruned = 0
        for a in list(self.edges):
            for b in list(self.edges[a]):
                edge = self.edges[a][b]
                edge["weight"] = round(self._finite(
                    edge.get("weight", 1.0), 1.0, 0.0, 1.0)
                    * EDGE_DECAY_PER_DREAM, 4)
                if edge["weight"] < EDGE_PRUNE_THRESHOLD:
                    del self.edges[a][b]
                    pruned += 1
            if not self.edges[a]:
                del self.edges[a]
        self.meta["last_edge_decay"] = today
        self._save()
        return pruned

    @staticmethod
    def _id(text):
        # content addressing only — no security property is derived from
        # the hash; md5[:12] keeps existing graphs' node ids stable
        return _content_md5(text.encode("utf-8")).hexdigest()[:12]

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
        if is_query:
            self._ensure_related()
        if is_query and self.related is not None:
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
        cleaned = re.sub(
            u"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]",
            "", text or "")
        # Lone surrogates cannot be encoded as UTF-8 and otherwise survive
        # JSON repair until a later save/export crashes.
        cleaned = re.sub(u"[\ud800-\udfff]", "", cleaned)
        return cleaned.strip()

    @classmethod
    def _validated_text(cls, text, label="memory"):
        if not isinstance(text, str):
            raise ValueError("%s text must be a string" % label)
        cleaned = cls._clean_text(text)
        if not cleaned:
            raise ValueError("%s text must not be empty" % label)
        if len(cleaned) > MAX_TEXT_CHARS:
            raise ValueError("%s text exceeds %d chars" % (
                label, MAX_TEXT_CHARS))
        return cleaned

    @classmethod
    def _validated_query(cls, query, label="query"):
        if not isinstance(query, str):
            raise ValueError("%s must be a string" % label)
        cleaned = cls._clean_text(query)
        if not cleaned:
            raise ValueError("%s must not be empty" % label)
        if len(cleaned) > MAX_QUERY_CHARS:
            raise ValueError("%s exceeds %d chars" % (
                label, MAX_QUERY_CHARS))
        return cleaned

    # -- write path ---------------------------------------------------
    def remember(self, text, confidence=1.0, metadata=None):
        with self._transaction():
            return self._remember(text, confidence, metadata)

    def remember_many(self, records):
        """Store many facts with one lock, graph commit, and durable batch."""
        records = list(records)
        if not records:
            return []
        if len(records) > MAX_NODES:
            raise FileLimitError("batch exceeds %d records" % MAX_NODES)
        node_ids = []
        with self._transaction():
            for record in records:
                if isinstance(record, str):
                    text, confidence = record, 1.0
                elif isinstance(record, dict):
                    text = record.get("text")
                    confidence = record.get("confidence", 1.0)
                    metadata = {
                        key: record[key] for key in (
                            "type", "scope", "authority", "source_trust",
                            "sensitivity", "expires_at", "pinned",
                            "entity", "attr")
                        if key in record
                    }
                else:
                    raise ValueError(
                        "batch records must be strings or objects")
                node_ids.append(self._remember(
                    text, confidence,
                    metadata if isinstance(record, dict) else None))
        return node_ids

    @classmethod
    def _memory_metadata(cls, metadata, by):
        metadata = metadata if isinstance(metadata, dict) else {}
        memory_type = metadata.get("type", "semantic")
        if memory_type not in MEMORY_TYPES:
            raise ValueError("invalid memory type: %s" % memory_type)
        scope = metadata.get("scope", "project")
        if scope not in MEMORY_SCOPES:
            raise ValueError("invalid memory scope: %s" % scope)
        trust = metadata.get("source_trust", "user")
        if trust not in MEMORY_TRUST:
            raise ValueError("invalid source trust: %s" % trust)
        sensitivity = metadata.get("sensitivity", "internal")
        if sensitivity not in MEMORY_SENSITIVITY:
            raise ValueError(
                "invalid sensitivity: %s" % sensitivity)
        expires = metadata.get("expires_at")
        if expires is not None:
            expires = cls._iso_timestamp(expires, None)
            if expires is None:
                raise ValueError("expires_at must be an ISO timestamp")
        return {
            "type": memory_type,
            "scope": scope,
            "authority": cls._metadata_text(
                metadata.get("authority"), by, 80),
            "source_trust": trust,
            "sensitivity": sensitivity,
            "expires_at": expires,
            "pinned": bool(metadata.get("pinned", False)),
            "entity": cls._metadata_text(
                metadata.get("entity"), None, 120)
            if metadata.get("entity") is not None else None,
            "attr": cls._metadata_text(
                metadata.get("attr"), None, 120)
            if metadata.get("attr") is not None else None,
        }

    def _remember(self, text, confidence=1.0, metadata=None):
        text = self._validated_text(text)
        confidence = self._finite(confidence, 1.0, 0.0, 1.0)
        nid = self._id(text)
        now = _now().isoformat()
        is_new = nid not in self.nodes
        if nid in self.nodes:
            n = self.nodes[nid]
            # Reopening and ordinary duplicate reinforcement use the same
            # constant, so one action has one persisted meaning.
            n["weight"] = min(1.0, n["weight"] + BOOST_PER_ACCESS)
            n["peak_weight"] = max(n.get("peak_weight", 1.0), n["weight"])
            n["access_count"] = n.get("access_count", 0) + 1
            n["last_accessed"] = now
            old_confidence = n.get("confidence", 1.0)
            n["confidence"] = max(old_confidence, confidence)
            if metadata:
                by = n.get("origin", {}).get("by", "agent")
                n.update(self._memory_metadata(metadata, by))
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
        else:
            by, session = self._actor()
            typed = self._memory_metadata(metadata, by)
            self.nodes[nid] = {
                "text": text,
                "weight": 1.0,
                "peak_weight": 1.0,
                "created": now,
                "last_accessed": now,
                "access_count": 0,
                "confidence": confidence,
                "keys": self._extract_keys(text),
                "history": [],
                # provenance + truth validity, written the moment the
                # fact is learned (structure at write time)
                "origin": {"by": by, "session": session, "via": "remember"},
                "valid_from": now,
                "valid_to": None,
                **typed,
            }
            self.edges.setdefault(nid, {})
            self.related = None    # rebuild lazily; avoids O(N^2) per write
        self._save()
        # journal AFTER the save: the provenance log records only facts
        # that actually landed on disk
        if is_new:
            node = self.nodes[nid]
            self._journal(
                "remember", id=nid, text=text,
                type=node.get("type"), scope=node.get("scope"),
                authority=node.get("authority"),
                source_trust=node.get("source_trust"),
                sensitivity=node.get("sensitivity"),
                expires_at=node.get("expires_at"),
                pinned=node.get("pinned", False),
                entity=node.get("entity"), attr=node.get("attr"))
        else:
            self._journal("remember", id=nid, dup=True)
        self._log_signal("remember", text)
        return nid

    def link(self, text_a, text_b, relation="related"):
        with self._transaction():
            return self._link(text_a, text_b, relation)

    def _link(self, text_a, text_b, relation="related"):
        # relations end up in graph.json and journals — same control-char
        # hygiene as memory texts (auditor finding), plus a sane length cap
        if not isinstance(relation, str):
            raise ValueError("relation must be a string")
        relation = " ".join(self._clean_text(relation).split())[:60] or "related"
        inverse = DIRECTED_RELATIONS.get(relation)
        directed = inverse is not None
        # hash the CLEANED text, exactly as remember() does, so the edge is
        # stored under the same id the node gets — otherwise the edge points
        # at a phantom id and is dropped on next load (auditor finding)
        # Validate BOTH endpoints before remember() can persist either one:
        # a rejected second endpoint must not leave a partial first memory.
        text_a = self._validated_text(text_a, "first link endpoint")
        text_b = self._validated_text(text_b, "second link endpoint")
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
        # Linking is a real use of both memories. Refresh their grace window
        # without pretending they were recalled/confirmed.
        for nid in (id_a, id_b):
            self.nodes[nid]["last_accessed"] = now
        forward = {
            "relation": relation,
            "weight": 1.0,
            "created": now,
            "directed": directed,
        }
        reverse = {
            "relation": inverse if directed else relation,
            "weight": 1.0,
            "created": now,
            "directed": directed,
        }
        if directed:
            forward["inverse_relation"] = inverse
            reverse["inverse_relation"] = relation
        self.edges.setdefault(id_a, {})[id_b] = forward
        self.edges.setdefault(id_b, {})[id_a] = reverse
        self._save()
        self._journal("link", id=id_a, other=id_b, relation=relation)
        self._log_signal("link", "%s --%s--> %s" % (text_a, relation, text_b))
        arrow = "->" if directed else "<->"
        return "linked: %s %s %s" % (text_a, arrow, text_b)

    def _resolve_node_ref(self, value):
        if isinstance(value, str) and value in self.nodes:
            return value
        text = self._validated_text(value, "memory reference")
        node_id = self._id(text)
        return node_id if node_id in self.nodes else None

    def forget(self, node_id, reason="user requested"):
        with self._transaction():
            if node_id not in self.nodes:
                return False
            node = self.nodes[node_id]
            node["forgotten_at"] = _now().isoformat()
            node["forgotten_reason"] = self._metadata_text(
                reason, "user requested", 160)
            self._save()
            self._journal(
                "forget", id=node_id,
                reason=node["forgotten_reason"])
            return True

    def unlink(self, a, b):
        with self._transaction():
            left = self._resolve_node_ref(a)
            right = self._resolve_node_ref(b)
            if left is None or right is None:
                return False
            changed = False
            if right in self.edges.get(left, {}):
                del self.edges[left][right]
                changed = True
            if left in self.edges.get(right, {}):
                del self.edges[right][left]
                changed = True
            for node_id in (left, right):
                if node_id in self.edges and not self.edges[node_id]:
                    del self.edges[node_id]
            if changed:
                self._save()
                self._journal("unlink", id=left, other=right)
            return changed

    def _redact_node(self, node_id, replacement, reason, digest):
        node = self.nodes.get(node_id)
        if node is None:
            return []
        originals = [node.get("text", "")]
        for entry in node.get("history", []):
            if isinstance(entry, dict) and isinstance(
                    entry.get("text"), str):
                originals.append(entry["text"])
                entry["text"] = replacement
        node["text"] = replacement
        node["keys"] = []
        node["redacted"] = {
            "digest": digest,
            "reason": self._metadata_text(reason, "redacted", 160),
            "at": _now().isoformat(),
        }
        self.related = None
        self._save()
        return [text for text in originals if text]

    def _purge_node(self, node_id):
        node = self.nodes.get(node_id)
        if node is None:
            return []
        originals = [node.get("text", "")]
        originals.extend(
            entry.get("text", "")
            for entry in node.get("history", [])
            if isinstance(entry, dict)
        )
        del self.nodes[node_id]
        self.edges.pop(node_id, None)
        for source in list(self.edges):
            self.edges[source].pop(node_id, None)
            if not self.edges[source]:
                del self.edges[source]
        self.related = None
        self._save()
        return [text for text in originals if text]

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
                rev = self.edges.get(nbr, {})
                if rev.get(nid, {}).get("relation") == "supersedes":
                    del rev[nid]
                    if not rev:
                        del self.edges[nbr]
        if nid in self.edges and not self.edges[nid]:
            del self.edges[nid]

    def correct(self, old_hint, new_text):
        with self._transaction():
            return self._correct(old_hint, new_text)

    def _correct(self, old_hint, new_text):
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
        tokens with the hint. A one-token hint is accepted only when it
        identifies exactly one current fact."""
        # same control-char hygiene and hashing as remember(): correct is a
        # write path too, and an uncleaned new_text would store text under
        # an id remember() would never produce (auditor finding). An empty
        # replacement would silently blank a memory — refuse it.
        old_hint = self._validated_query(old_hint, "correction hint")
        new_text = self._validated_text(new_text, "corrected")
        if not self.nodes:
            return None
        results, _, _ = self.recall(old_hint, top_k=1)
        if not results:
            return None
        nid, _, node = results[0]
        hint_toks = self._content_tokens(old_hint)
        shared = hint_toks & self._content_tokens(node["text"])
        exact_hint = " ".join(old_hint.lower().split()) == \
            " ".join(node["text"].lower().split())
        if not (exact_hint or len(shared) >= 2):
            unique_short = False
            if len(hint_toks) == 1 and len(shared) == 1:
                matching = [
                    candidate_id for candidate_id, candidate in
                    self.nodes.items()
                    if self._valid_at(candidate) and
                    hint_toks <= self._content_tokens(candidate["text"])
                ]
                unique_short = matching == [nid]
            if not unique_short:
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
            if nbr not in self.edges.setdefault(new_nid, {}):
                self.edges[new_nid][nbr] = dict(e)
            rev = self.edges.get(nbr, {}).get(nid)
            if rev is not None and rev.get("relation") not in (
                    "supersedes", "superseded-by"):
                if new_nid not in self.edges.setdefault(nbr, {}):
                    self.edges[nbr][new_nid] = dict(rev)
        # the explicit, timestamped state transition
        self.edges.setdefault(new_nid, {})[nid] = {
            "relation": "supersedes", "weight": 0.5, "created": now}
        self.edges.setdefault(nid, {})[new_nid] = {
            "relation": "superseded-by", "weight": 0.5, "created": now}
        # close the old fact explicitly; this preserves lineage but is not
        # a general rollback mechanism and is distinct from attention decay
        node["last_accessed"] = now
        node["valid_to"] = now
        node["superseded_by"] = new_nid
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
        query = self._validated_query(query)
        self.last_recall_explain = {}
        top_k = max(1, min(50, int(self._finite(top_k, RECALL_TOP_K, 1, 50))))
        max_hops = max(0, min(10, int(self._finite(
            max_hops, RECALL_RADIUS, 0, 10))))
        rrf_k = max(1, min(10_000, int(self._finite(rrf_k, 60, 1, 10_000))))
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
            self.last_recall_explain = {
                "query": query, "reason": "no indexable keys",
                "results": {}}
            return [], (time.perf_counter() - t0) * 1000, {}
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
            self.last_recall_explain = {
                "query": query, "reason": "no matching activation",
                "results": {}}
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
            sorted(direct.items(), key=lambda x: (-x[1], x[0])))}
        sr = {n: i for i, (n, _) in enumerate(
            sorted(spread.items(), key=lambda x: (-x[1], x[0])))}
        dr_default, sr_default = len(dr) + 1, len(sr) + 1
        fused = {}
        for nid in set(direct) | set(spread):
            fused[nid] = (1.0 / (rrf_k + dr.get(nid, dr_default)) +
                          1.0 / (rrf_k + sr.get(nid, sr_default)))
        fused_before_semantic = dict(fused)

        # lexical-semantic re-rank of the head (offline hash embeddings).
        # Defense in depth: activation can only reach ids absent from
        # self.nodes if the graph was mutated externally mid-flight —
        # drop them instead of raising.
        fused = {nid: s for nid, s in fused.items() if nid in alive}
        ranked = sorted(fused.items(), key=lambda x: (-x[1], x[0]))
        # identity questions ("what is my name") are decided by lexical
        # identity evidence; the char-gram rerank favors token repetition
        # ("file name ... class name") and must sit this one out
        # (auditor finding)
        if len(ranked) > 1 and not identity_q:
            reranked = []
            head = ranked[:top_k * 3]
            similarities = self.reranker.similarities(
                query, [self.nodes[nid]["text"] for nid, _ in head])
            semantic_scores = dict(
                (nid, similarity)
                for (nid, _), similarity in zip(head, similarities))
            for (nid, base), sim in zip(head, similarities):
                reranked.append((nid, base * (1.0 + sim)))
            reranked.sort(key=lambda x: (-x[1], x[0]))
            ranked = reranked
        else:
            semantic_scores = {}

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
        self.last_recall_explain = {
            "query": query,
            "backend": dict(self.reranker.last_report),
            "results": {
                nid: {
                    "direct": direct.get(nid, 0.0),
                    "spread": spread.get(nid, 0.0),
                    "fused": fused_before_semantic.get(nid, 0.0),
                    "semantic": semantic_scores.get(nid),
                    "final": score,
                    "kind": kinds[nid],
                    "valid_from": self.nodes[nid].get("valid_from"),
                    "valid_to": self.nodes[nid].get("valid_to"),
                }
                for nid, score, _ in results
            },
        }
        return results, (time.perf_counter() - t0) * 1000, kinds

    def bump(self, node_ids):
        with self._transaction():
            return self._bump(node_ids)

    def _bump(self, node_ids):
        """Reinforce nodes after a confirmed recall (kept separate from
        recall() so reads stay pure; the `confirm` CLI command is the
        agent-facing path here). Tracks peak_weight for Ebbinghaus, and
        restrengthens the confirmed node's edges — connections you actually
        use stay strong, unused ones decay away dream by dream."""
        node_ids = list(dict.fromkeys(node_ids))
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
                changed = True
        if changed:
            self._save()
            self._journal("confirm", ids=[nid for nid in node_ids
                                          if nid in self.nodes])
        return changed

    def decay(self, dry_run=False):
        with self._transaction():
            return self._decay(dry_run=dry_run)

    def _decay(self, dry_run=False):
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
            if n.get("pinned"):
                continue
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
            type_factor = {
                "semantic": 1.0,
                "episodic": 0.75,
                "procedural": 1.5,
                "decision": 2.0,
            }.get(n.get("type"), 1.0)
            stability = (
                STABILITY_BASE_DAYS + access * STABILITY_PER_ACCESS
            ) * type_factor
            retention = math.exp(-days / stability)
            # clamp to [0,1] like every other weight-mutating path (auditor
            # finding: decay was the only one without an upper clamp)
            new_weight = max(0.0, min(1.0, n.get("peak_weight", 1.0) * retention))
            if not dry_run:
                n["weight"] = new_weight
            if new_weight < WEIGHT_THRESHOLD and access < 2 and days > GRACE_DAYS:
                pruned.append((nid, n["text"]))
        bounded = []
        batch_bytes = 0
        for item in pruned[:MAX_PRUNES_PER_CYCLE]:
            item_bytes = len(json.dumps(
                item, ensure_ascii=False).encode("utf-8"))
            if bounded and batch_bytes + item_bytes > MAX_PRUNE_BATCH_BYTES:
                break
            bounded.append(item)
            batch_bytes += item_bytes
        if dry_run:
            return [t for _, t in bounded]
        pruned = bounded
        if pruned:
            if self._archive_preflight():
                for nid, _ in pruned:
                    del self.nodes[nid]
                    self.edges.pop(nid, None)
                    for other in self.edges.values():
                        other.pop(nid, None)
                self._queue_prune(pruned, now)
            else:
                print("warning: archive.md is unsafe or not writable; "
                      "keeping %d prunable memories." % len(pruned),
                      file=sys.stderr)
                pruned = []
        self._save()
        return [t for _, t in pruned]

    def _archive_preflight(self):
        """Refuse pruning when the archive path is already unsafe."""
        arch = self.path.parent / "archive.md"
        try:
            _reject_symlinked_parents(arch, self.path.parent)
            if arch.is_symlink():
                return False
            if arch.exists():
                info = os.lstat(str(arch))
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    return False
                if not os.access(str(arch), os.W_OK):
                    return False
            return True
        except OSError:
            return False

    def _rotate_archive(self, arch, when):
        """Move the active archive aside in O(1), retaining monthly names."""
        month = when[:7] if isinstance(when, str) and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", when) else str(_now().date())[:7]
        for index in range(1_000_000):
            suffix = "" if index == 0 else "-%03d" % index
            target = arch.with_name("archive-%s%s.md" % (month, suffix))
            if target.exists() or target.is_symlink():
                continue
            _reject_symlinked_parents(target, self.path.parent)
            os.replace(str(arch), str(target))
            if os.name != "nt":
                try:
                    dfd = os.open(
                        str(self.path.parent),
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(dfd)
                    finally:
                        os.close(dfd)
                except OSError:
                    pass
            return target
        raise OSError("could not allocate an archive segment name")

    def _queue_prune(self, pruned, now):
        state = self._transaction_state
        payload = json.dumps(
            {"ids": [nid for nid, _ in pruned],
             "texts": [text for _, text in pruned],
             "date": str(now.date()), "pid": os.getpid(),
             "clock": time.time_ns()},
            ensure_ascii=False, sort_keys=True)
        state.prunes.append({
            "tx": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24],
            "ids": [nid for nid, _ in pruned],
            "texts": [text for _, text in pruned],
            "date": str(now.date()),
        })

    @property
    def _prune_outbox_path(self):
        return self.path.parent / PRUNE_OUTBOX_FILE

    def _quarantine_prune_outbox(self, reason):
        """Preserve a damaged recovery record without bricking all writes."""
        path = self._prune_outbox_path
        bak = path.with_name(
            "%s.corrupt-%s-%d" % (
                path.name, _now().strftime("%H%M%S%f"), os.getpid()))
        try:
            if os.name != "nt" and os.rename in getattr(
                    os, "supports_dir_fd", set()):
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | \
                    getattr(os, "O_NOFOLLOW", 0)
                parent_fd = os.open(str(path.parent), flags)
                try:
                    info = os.stat(
                        path.name, dir_fd=parent_fd,
                        follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise UnsafePathError(
                            "refusing to quarantine an unsafe prune outbox")
                    os.rename(
                        path.name, bak.name,
                        src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            else:
                info = os.lstat(str(path))
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise UnsafePathError(
                        "refusing to quarantine an unsafe prune outbox")
                path.rename(bak)
            print("warning: prune recovery outbox is corrupt (%s).\n"
                  "  corrupt copy saved as %s; continuing without replay."
                  % (reason, bak.name), file=sys.stderr)
            return True
        except (OSError, ValueError):
            print("warning: prune recovery outbox is unreadable or unsafe; "
                  "ignoring it without deleting it.", file=sys.stderr)
            return False

    def _remove_prune_outbox(self):
        """Unlink only the exact private regular outbox in `.mind/`."""
        path = self._prune_outbox_path
        if not path.exists() and not path.is_symlink():
            return
        if os.name != "nt" and os.unlink in getattr(
                os, "supports_dir_fd", set()):
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | \
                getattr(os, "O_NOFOLLOW", 0)
            parent_fd = os.open(str(path.parent), flags)
            try:
                info = os.stat(
                    path.name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise UnsafePathError(
                        "refusing to remove an unsafe prune outbox")
                os.unlink(path.name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(parent_fd)
            return
        fd = _open_regular(path, os.O_RDONLY, boundary=self.path.parent)
        try:
            opened = os.fstat(fd)
            current = os.lstat(str(path))
            if (opened.st_dev, opened.st_ino) != (
                    current.st_dev, current.st_ino):
                raise StaleTargetError(
                    "prune outbox changed before removal")
        finally:
            os.close(fd)
        path.unlink()

    def _read_prune_outbox(self):
        path = self._prune_outbox_path
        if not path.exists() or path.is_symlink():
            return []
        try:
            info = os.lstat(str(path))
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or \
                    info.st_size > 5_000_000:
                raise ValueError("unsafe prune outbox")
            data = json.loads(_read_text_retry(
                path, boundary=self.path.parent))
            if not isinstance(data, list):
                raise ValueError("prune outbox has the wrong structure")
        except (OSError, ValueError, json.JSONDecodeError,
                UnicodeError, RecursionError) as e:
            self._quarantine_prune_outbox(e)
            return []
        out = []
        for record in data:
            if not isinstance(record, dict):
                self._quarantine_prune_outbox("invalid record")
                return []
            tx = record.get("tx")
            ids = record.get("ids")
            texts = record.get("texts")
            date = record.get("date")
            if not (isinstance(tx, str) and re.fullmatch(r"[0-9a-f]{24}", tx)
                    and isinstance(ids, list) and isinstance(texts, list)
                    and len(ids) == len(texts)
                    and len(ids) <= MAX_PRUNES_PER_CYCLE
                    and all(isinstance(v, str) and
                            re.fullmatch(r"[0-9a-f]{12}", v)
                            for v in ids)
                    and all(isinstance(v, str) and
                            len(v) <= MAX_TEXT_CHARS + 13
                            for v in texts)
                    and isinstance(date, str)
                    and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)):
                self._quarantine_prune_outbox("invalid record fields")
                return []
            out.append({"tx": tx, "ids": ids, "texts": texts,
                        "date": date})
        return out

    def _write_prune_outbox(self, records):
        path = self._prune_outbox_path
        if records:
            payload = json.dumps(records, ensure_ascii=False, indent=2)
            if len(payload.encode("utf-8")) > MAX_PRUNE_OUTBOX_BYTES:
                raise FileLimitError("prune outbox exceeds %d bytes"
                                     % MAX_PRUNE_OUTBOX_BYTES)
            _atomic_write(path, payload, boundary=self.path.parent)
        else:
            self._remove_prune_outbox()

    def _stage_prunes(self, records):
        pending = self._read_prune_outbox()
        known = {r["tx"] for r in pending}
        pending.extend(r for r in records if r["tx"] not in known)
        self._write_prune_outbox(pending)

    def _recover_prune_outbox(self):
        """Finish or cancel crash-interrupted prune side effects."""
        pending = self._read_prune_outbox()
        if not pending:
            return
        keep = []
        for record in pending:
            # The outbox lands before graph.json. If any target is still in
            # the fresh graph, that graph commit never completed: cancel.
            if any(nid in self.nodes for nid in record["ids"]):
                continue
            if not self._archive(
                    record["texts"], record["date"], record["tx"]):
                keep.append(record)
                continue
            if not self._journal_has_prune(record["tx"]):
                if not self._journal_immediate(
                        "prune", ids=record["ids"], texts=record["texts"],
                        tx=record["tx"]):
                    keep.append(record)
        self._write_prune_outbox(keep)

    def _archive(self, texts, when, tx=None):
        """Forgotten, not destroyed: pruned memories append to archive.md.
        Returns True only when the archive write actually happened."""
        arch = self.path.parent / "archive.md"
        if not self._archive_preflight():
            return False
        marker = "<!-- mind-prune:%s -->" % tx if tx else None
        if marker and arch.exists():
            try:
                if marker in _read_tail_text(
                        arch, RECEIPT_TAIL_BYTES, self.path.parent):
                    return True
            except (OSError, UnicodeError, ValueError):
                return False
        lines = ["\n## forgotten on %s\n" % when]
        lines += ["- %s" % t for t in texts]
        if marker:
            lines.append(marker)
        header = "" if arch.exists() else \
            "# mind archive — memories pruned by decay (restore with `remember`)\n"
        payload = (header + "\n".join(lines) + "\n").encode("utf-8")
        try:
            if arch.exists() and (
                    os.lstat(str(arch)).st_size + len(payload)
                    > ARCHIVE_ROTATE_BYTES):
                self._rotate_archive(arch, when)
                header = (
                    "# mind archive — memories pruned by decay "
                    "(restore with `remember`)\n"
                )
                payload = (header + "\n".join(lines) + "\n").encode("utf-8")
        except (OSError, ValueError):
            return False
        # APPEND, don't rewrite: the archive only grows, and rewriting the
        # whole file per prune batch is O(archive) forever (auditor
        # finding). Same trust-boundary checks as every other write.
        try:
            _append_regular(
                arch, payload,
                boundary=self.path.parent, durable=True)
        except (OSError, ValueError):
            return False
        return True

    def _journal_has_prune(self, tx):
        path = self.path.parent / JOURNAL_FILE
        if not path.exists() or path.is_symlink():
            return False
        try:
            tail = _read_tail_text(
                path, RECEIPT_TAIL_BYTES, self.path.parent)
        except (OSError, ValueError, UnicodeError):
            return False
        for line in reversed(tail.splitlines()):
            if tx not in line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                continue
            if event.get("op") == "prune" and event.get("tx") == tx:
                return True
        return False

    def _log_signal(self, kind, content):
        if self._transaction_active():
            self._transaction_state.signals.append((kind, content))
            return
        self._log_signal_immediate(kind, content)

    def _log_signal_immediate(self, kind, content):
        self._log_signals_immediate([(kind, content)])

    def _log_signals_immediate(self, records):
        if not records:
            return
        # same O_NOFOLLOW discipline as every other write path: the
        # is_symlink() check alone is TOCTOU-raceable (auditor finding,
        # 6.2.1 — this was the one append still using a plain open())
        sig_file = self.path.parent / SIGNALS_FILE
        if sig_file.is_symlink():
            return
        payload = "".join(
            json.dumps(
                {"kind": kind, "content": content,
                 "ts": _now().isoformat()},
                ensure_ascii=False) + "\n"
            for kind, content in records
        ).encode("utf-8")
        try:
            _append_regular(
                sig_file, payload, boundary=self.path.parent)
        except (OSError, ValueError):
            pass    # telemetry only — never block the write it rode on
        try:
            _scheduler_note_signals(self.path.parent, len(records))
        except (OSError, ValueError):
            print("warning: automatic-maintenance scheduler could not "
                  "record a write signal.", file=sys.stderr)

    # -- provenance -----------------------------------------------------
    @classmethod
    def _actor(cls):
        """Who is writing. Agents/harnesses set MIND_BY and MIND_SESSION
        in the environment; the zero-setup default is 'agent'."""
        return (
            cls._metadata_text(os.environ.get("MIND_BY"), "agent", 80),
            cls._metadata_text(os.environ.get("MIND_SESSION"), None, 120),
        )

    def _journal(self, op, **fields):
        """Append-only provenance log (journal.jsonl). Unlike
        signals.jsonl (telemetry, consumed by dream), the journal is NEVER
        rotated or deleted: every fact-mutating operation records who,
        when, and what, so "where did this fact come from" stays
        answerable for the life of the project. Journal failure warns but
        never blocks a memory write (availability over completeness —
        documented tradeoff)."""
        if self._transaction_active():
            self._transaction_state.journal.append((op, fields))
            return True
        return self._journal_immediate(op, **fields)

    def _journal_immediate(self, op, **fields):
        return self._journal_batch_immediate([(op, fields)])

    def _journal_batch_immediate(self, records):
        if not records:
            return True
        jf = self.path.parent / JOURNAL_FILE
        # same trust boundary as every other write: a symlinked journal
        # OR a symlinked .mind root must never leak a file outside the
        # project (the lock file had this exact hole once — test finding)
        try:
            _reject_symlinked_parents(jf, self.path.parent)
        except ValueError:
            print("warning: .mind is unsafe (symlink?); skipping "
                  "provenance entry.", file=sys.stderr)
            return False
        if jf.is_symlink():
            print("warning: journal.jsonl is a symlink; skipping "
                  "provenance entry.", file=sys.stderr)
            return False
        by, session = self._actor()
        timestamp = _now().isoformat()
        entries = []
        for op, fields in records:
            entry = {
                "format": 2,
                "ts": timestamp,
                "ts_utc_ns": _utc_ns(),
                "op": op,
                "by": by,
            }
            if session:
                entry["session"] = session
            entry.update(fields)
            identity_payload = json.dumps(
                entry, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"))
            entry["event_id"] = hashlib.sha256(
                identity_payload.encode("utf-8")).hexdigest()[:24]
            entries.append(entry)
        payload = "".join(
            json.dumps(entry, ensure_ascii=False) + "\n"
            for entry in entries
        ).encode("utf-8")
        try:
            # single O_APPEND os.write: concurrent writers cannot
            # interleave a line on a local filesystem (auditor finding:
            # the provenance log was the one unlocked write path)
            _append_regular(
                jf, payload,
                boundary=self.path.parent, durable=True)
            return True
        except (OSError, ValueError) as e:
            print("warning: journal.jsonl not writable (%s); provenance "
                  "entry lost." % e, file=sys.stderr)
            return False

    @staticmethod
    def _event_mentions(event, node_id):
        if node_id in (event.get("id"), event.get("old_id"),
                       event.get("new_id"), event.get("other")):
            return True
        ids = event.get("ids")
        return isinstance(ids, list) and node_id in ids

    def journal_entries(self, node_id=None, tail_bytes=10_000_000):
        """Read current and segmented provenance as one logical journal."""
        if node_id is not None:
            if not isinstance(node_id, str) or len(node_id) > 128:
                return JournalEntries()
        paths = []
        segment_dir = self.path.parent / JOURNAL_DIR
        if segment_dir.is_dir() and not segment_dir.is_symlink():
            paths.extend(
                path for path in sorted(segment_dir.glob("*.jsonl"))
                if path.is_file() and not path.is_symlink())
        current = self.path.parent / JOURNAL_FILE
        if current.exists() and not current.is_symlink():
            paths.append(current)
        if not paths:
            return JournalEntries()
        if node_id is not None:
            budget = MAX_JOURNAL_SCAN_BYTES
            selected = []
            for path in reversed(paths):
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if selected and size > budget:
                    break
                selected.append(path)
                budget -= min(size, budget)
                if budget <= 0:
                    break
            paths = list(reversed(selected))
        out = deque(maxlen=MAX_JOURNAL_MATCHES)
        total = 0
        for path in paths:
            try:
                fd = _open_regular(
                    path, os.O_RDONLY, boundary=self.path.parent)
            except (OSError, ValueError):
                continue
            with os.fdopen(fd, "rb") as handle:
                size = os.fstat(handle.fileno()).st_size
                if node_id is not None and len(paths) == 1 and \
                        size > MAX_JOURNAL_SCAN_BYTES:
                    handle.seek(size - MAX_JOURNAL_SCAN_BYTES)
                    handle.readline()
                for raw in handle:
                    try:
                        event = json.loads(raw.decode("utf-8", "replace"))
                    except (json.JSONDecodeError, UnicodeDecodeError,
                            RecursionError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    op = event.get("op")
                    if not isinstance(op, str):
                        continue
                    clean = {"op": _display_text(op, 40)}
                    for field in (
                            "ts", "by", "session", "id", "old_id",
                            "new_id", "other", "relation", "text",
                            "old_text", "new_text", "tx",
                            "target_digest", "reason", "event_id"):
                        value = event.get(field)
                        if isinstance(value, str):
                            clean[field] = _display_text(
                                value,
                                MAX_TEXT_CHARS if "text" in field else 160)
                    for field in ("ids", "texts"):
                        value = event.get(field)
                        if isinstance(value, list):
                            clean[field] = [
                                _display_text(item, MAX_TEXT_CHARS)
                                for item in value if isinstance(item, str)
                            ][:MAX_PRUNES_PER_CYCLE]
                    if isinstance(event.get("ts_utc_ns"), int):
                        clean["ts_utc_ns"] = event["ts_utc_ns"]
                    if node_id is None or self._event_mentions(
                            clean, node_id):
                        out.append(clean)
                        total += 1
        return JournalEntries(out, total_count=total)

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
        if node.get("forgotten_at"):
            return False
        expires = node.get("expires_at")
        if expires and (at or _now().isoformat()) >= expires:
            return False
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
