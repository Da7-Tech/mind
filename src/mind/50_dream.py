# The Dreamer — sleep cycle between sessions
# ────────────────────────────────────────────────────────────────
class Dreamer:
    """light sleep (ingest signals) -> deep sleep (Ebbinghaus decay +
    synaptic pruning) -> REM (cluster, promote, detect contradictions).

    Fully deterministic: no LLM calls, no network, every action explained
    in the dream journal. Run with dry_run=True to preview."""

    def __init__(self, mind_dir, hippo, cortex):
        mind_dir = Path(mind_dir)
        self.dir = mind_dir / DREAMS_DIR
        self.hippo = hippo
        self.cortex = cortex
        self.signals_file = mind_dir / SIGNALS_FILE

    def dream(self, dry_run=False):
        promotion_plans = []
        with self.hippo._transaction():
            memo_text, signal_snapshot = self._dream(
                dry_run=dry_run, promotion_plans=promotion_plans)
            if dry_run:
                return None, memo_text
            # Commit graph.json first but retain its lock until every derived
            # artifact is complete. A later dream cannot overtake this one.
            self.hippo._flush_transaction()
            failures = []
            for topic, content in promotion_plans:
                try:
                    self.cortex.promote(topic, content)
                except (OSError, ValueError, UnicodeError) as e:
                    failures.append("%s (%s)" % (
                        _display_text(topic, 80), _display_text(e, 120)))
            if failures:
                memo_text += (
                    "\n## Post-commit notes\n"
                    "- cortex promotion skipped: %s\n"
                    % "; ".join(failures))
            result = self._write_journal(memo_text)
            self._consume_signals(signal_snapshot)
            return result

    def _dream(self, dry_run=False, promotion_plans=None):
        mode = " (dry run — nothing written)" if dry_run else ""
        log = ["# Dream journal — %s%s" % (_now().date(), mode), ""]
        log.append("_cycle started %s_" % _now().strftime("%H:%M"))

        # 1. light sleep: count the session's write signals (telemetry). The
        # consolidation inputs are the node/edge weights themselves, not the
        # signal log — so this is reported, then cleared, not replayed.
        signals, signal_snapshot = self._read_signals()
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
        # The once-per-day decision and weight updates happen inside the graph
        # lock. A stale concurrent dream therefore cannot double-decay the day
        # or overwrite a link/confirmation that landed while it was waiting.
        pruned_edges = self.hippo.decay_edges(dry_run=dry_run)
        if pruned_edges:
            log.append("- synaptic pruning: %s %d weak edges."
                       % ("would remove" if dry_run else "removed", pruned_edges))

        # 3. REM: cluster related memories and promote recurring themes.
        # Clustering uses offline hash embeddings — deterministic, no network.
        promoted = self._rem_promote(
            log, dry_run, promotion_plans=promotion_plans)

        # 4. REM: contradiction scan (feeds reconsolidation)
        conflicts = self._rem_conflicts(log, dry_run)

        log.append("\n## Summary")
        log.append("- nodes: %d | pruned: %d | promoted clusters: %d | "
                   "conflicts flagged: %d"
                   % (len(self.hippo.nodes), len(pruned), len(promoted),
                      len(conflicts)))

        return "\n".join(log) + "\n", signal_snapshot

    def _write_journal(self, memo_text):
        """Write derived dream artifacts only after graph commit succeeds."""
        memo = self.dir / ("%s.md" % _now().date())
        # boundary = .mind/ so a symlinked dreams/ dir can't redirect the
        # journal write outside the project (auditor finding). If the dir is
        # unsafe, the consolidation already happened — just skip the journal
        # rather than crash with a traceback.
        try:
            if self.dir.is_symlink():
                raise ValueError("dreams directory is a symlink")
            _secure_mkdirs(self.dir, self.hippo.path.parent)
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
            _append_regular(
                memo, payload.encode("utf-8"),
                boundary=self.hippo.path.parent, durable=True)
        except (OSError, ValueError):
            print("warning: .mind/dreams is unsafe (symlink?); "
                  "skipping dream journal for this run.", file=sys.stderr)
            return None, memo_text
        return str(memo.relative_to(self.hippo.path.parent)), memo_text

    def _rem_promote(self, log, dry_run, promotion_plans=None):
        emb = self.hippo.embedder
        clusters = []
        by_key = defaultdict(set)
        comparisons = 0
        for nid in sorted(self.hippo.nodes):
            n = self.hippo.nodes[nid]
            if not self.hippo._valid_at(n):   # closed facts don't cluster
                continue
            if (n.get("source_trust") == "untrusted"
                    or n.get("sensitivity") in ("sensitive", "secret")):
                continue
            placed = False
            candidates = set()
            for key in n.get("keys", []):
                candidates.update(by_key.get(key, ()))
            for index in sorted(candidates):
                if comparisons >= MAX_DREAM_COMPARISONS:
                    break
                comparisons += 1
                c = clusters[index]
                if emb.similarity(n["text"], c["centroid"]) > CLUSTER_SIM:
                    c["members"].append(nid)
                    for key in n.get("keys", []):
                        by_key[key].add(index)
                    placed = True
                    break
            if not placed:
                index = len(clusters)
                clusters.append({"centroid": n["text"], "members": [nid]})
                for key in n.get("keys", []):
                    by_key[key].add(index)
        promoted = []
        log.append("\n## REM — consolidation")
        for c in clusters:
            if len(c["members"]) >= PROMOTION_THRESHOLD:
                texts = [self.hippo.nodes[m]["text"] for m in c["members"][:5]]
                topic = c["centroid"][:50]
                if not dry_run:
                    if promotion_plans is not None:
                        promotion_plans.append((
                            topic, "\n".join("- %s" % t for t in texts)))
                promoted.append(topic)
                log.append("- %s cluster (%d memories) -> cortex: %s"
                           % ("would promote" if dry_run else "selected",
                              len(c["members"]), topic))
        if not promoted:
            log.append("- no cluster reached the promotion threshold (%d)."
                       % PROMOTION_THRESHOLD)
        if comparisons >= MAX_DREAM_COMPARISONS:
            log.append("- promotion comparison budget reached (%d)."
                       % MAX_DREAM_COMPARISONS)
        return promoted

    def _rem_conflicts(self, log, dry_run):
        """Deterministic contradiction scan: two memories about the same
        subject (shared rare keys) that are similar but not near-identical
        are flagged and linked, never auto-deleted. The user (or agent)
        resolves them with `mind correct`."""
        emb = self.hippo.embedder
        # a superseded fact conflicting with its successor is not a
        # contradiction — it's history. Scan only currently-valid facts.
        nodes = [(nid, self.hippo.nodes[nid])
                 for nid in sorted(self.hippo.nodes)
                 if self.hippo._valid_at(self.hippo.nodes[nid])]
        N = max(1, len(nodes))
        df = defaultdict(int)
        for _, n in nodes:
            for k in set(n.get("keys", [])):
                df[k] += 1
        rare_members = defaultdict(list)
        rare_cutoff = max(2, N // 4)
        for nid, n in nodes:
            for key in set(n.get("keys", [])):
                if df[key] <= rare_cutoff:
                    rare_members[key].append(nid)
        node_map = dict(nodes)
        pair_hits = Counter()
        slot_pairs = set()
        pair_work = 0
        for key in sorted(rare_members):
            members = sorted(rare_members[key])
            for i, ida in enumerate(members):
                for idb in members[i + 1:]:
                    pair_hits[(ida, idb)] += 1
                    pair_work += 1
                    if pair_work >= MAX_DREAM_COMPARISONS:
                        break
                if pair_work >= MAX_DREAM_COMPARISONS:
                    break
            if pair_work >= MAX_DREAM_COMPARISONS:
                break
        slots = defaultdict(list)
        for node_id, node in nodes:
            entity = node.get("entity")
            attr = node.get("attr")
            if entity and attr:
                slots[(entity, attr)].append(node_id)
        for slot in sorted(slots):
            members = sorted(slots[slot])
            for index, left in enumerate(members):
                for right in members[index + 1:]:
                    if node_map[left].get("text") == node_map[right].get(
                            "text"):
                        continue
                    pair = (left, right)
                    pair_hits[pair] = max(pair_hits[pair], 2)
                    slot_pairs.add(pair)
        conflicts = []
        log.append("\n## REM — contradiction scan")
        for (ida, idb), shared_count in sorted(pair_hits.items()):
            if shared_count < 2:
                continue
            a, b = node_map[ida], node_map[idb]
            sim = emb.similarity(a["text"], b["text"])
            slot_conflict = (ida, idb) in slot_pairs
            if slot_conflict or 0.35 <= sim < 0.9:
                conflicts.append((ida, idb))
                if not dry_run:
                    # flag, never clobber: an existing user link between
                    # the pair keeps its relation and earned weight.
                    fwd = self.hippo.edges.get(ida, {}).get(idb)
                    rev = self.hippo.edges.get(idb, {}).get(ida)
                    user_edge = any(
                        e is not None and
                        e.get("relation") != "possible-conflict"
                        for e in (fwd, rev))
                    existing_conflict = any(
                        e is not None and
                        e.get("relation") == "possible-conflict"
                        for e in (fwd, rev))
                    conflict_fields = {
                        "conflict_kind": (
                            "slot" if slot_conflict else "lexical"),
                    }
                    if slot_conflict:
                        conflict_fields.update({
                            "conflict_entity": a.get("entity"),
                            "conflict_attr": a.get("attr"),
                        })
                    if not user_edge and not existing_conflict:
                        now_iso = _now().isoformat()
                        self.hippo.edges.setdefault(ida, {})[idb] = {
                            "relation": "possible-conflict",
                            "weight": 0.5, "created": now_iso,
                            "directed": False, **conflict_fields}
                        self.hippo.edges.setdefault(idb, {})[ida] = {
                            "relation": "possible-conflict",
                            "weight": 0.5, "created": now_iso,
                            "directed": False, **conflict_fields}
                    elif existing_conflict:
                        if slot_conflict:
                            for edge in (fwd, rev):
                                if edge is not None and edge.get(
                                        "relation") == "possible-conflict":
                                    edge.update(conflict_fields)
                        created = (
                            (fwd or {}).get("created")
                            or (rev or {}).get("created"))
                        if created:
                            log.append(
                                "    still flagged (first seen %s)"
                                % created[:19])
                label = (
                    "slot conflict %s.%s" % (
                        a.get("entity"), a.get("attr"))
                    if slot_conflict else "possible conflict")
                log.append("- %s (sim %.2f):" % (label, sim))
                log.append("    a: %s" % a["text"][:80])
                log.append("    b: %s" % b["text"][:80])
                log.append("    resolve with: mind correct \"<wrong>\" \"<right>\"")
        if not conflicts:
            log.append("- none found.")
        elif not dry_run:
            self.hippo._save()
        if pair_work >= MAX_DREAM_COMPARISONS:
            log.append("- contradiction candidate budget reached (%d)."
                       % MAX_DREAM_COMPARISONS)
        return conflicts

    def _read_signals(self):
        # symlink/size guards on the READ side too: dream must not follow
        # a symlinked signals file or slurp an absurdly large one
        # (auditor finding)
        empty = {"prefix": "", "identity": None,
                 "oversized": False, "bytes": 0}
        if not self.signals_file.exists() or self.signals_file.is_symlink():
            return [], empty
        try:
            content, identity = _read_text_retry(
                self.signals_file, max_bytes=MAX_SIGNALS_BYTES,
                with_identity=True, boundary=self.hippo.path.parent)
        except FileLimitError:
            try:
                info = os.lstat(str(self.signals_file))
                identity = (
                    info.st_dev, info.st_ino,
                    info.st_mtime_ns, info.st_size)
                size = info.st_size
            except OSError:
                identity, size = None, 0
            print("warning: signals.jsonl is unsafe or too large; "
                  "resetting bounded telemetry after this cycle.",
                  file=sys.stderr)
            return [], {
                "prefix": None, "identity": identity,
                "oversized": True, "bytes": size,
            }
        except (OSError, ValueError, UnicodeError):
            print("warning: signals.jsonl is unsafe; "
                  "leaving it untouched this cycle.", file=sys.stderr)
            return [], empty
        out = []
        for line in content.splitlines():
            try:
                out.append(json.loads(line))
            except (json.JSONDecodeError, RecursionError):
                continue
        return out, {
            "prefix": content, "identity": identity,
            "oversized": False, "bytes": len(content.encode("utf-8")),
        }

    def _consume_signals(self, consumed):
        """Remove only the exact signal prefix observed by this dream."""
        if isinstance(consumed, str):
            consumed = {
                "prefix": consumed, "identity": None,
                "oversized": False, "bytes": len(consumed.encode("utf-8")),
            }
        if not consumed:
            return
        if consumed.get("oversized"):
            identity = consumed.get("identity")
            if identity is None:
                return
            try:
                _atomic_write(
                    self.signals_file, "",
                    boundary=self.hippo.path.parent,
                    expected_identity=identity)
            except (OSError, ValueError):
                return
            self.hippo._journal_immediate(
                "signals-reset", bytes=consumed.get("bytes", 0))
            return
        prefix = consumed.get("prefix", "")
        if not prefix:
            return
        for _attempt in range(20):
            try:
                current, identity = _read_text_retry(
                    self.signals_file, max_bytes=MAX_SIGNALS_BYTES,
                    with_identity=True, boundary=self.hippo.path.parent)
            except FileNotFoundError:
                return
            except (OSError, ValueError, UnicodeError):
                return
            if not current.startswith(prefix):
                return
            try:
                _atomic_write(
                    self.signals_file, current[len(prefix):],
                    boundary=self.hippo.path.parent,
                    expected_identity=identity)
                return
            except StaleTargetError:
                continue
