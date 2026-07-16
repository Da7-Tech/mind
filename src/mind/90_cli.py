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
        self.pending_queue = None
        self.lifecycle = None
        self.storage = None
        self.user_dir = None
        self.user_hippo = None

    def init(self):
        # BEFORE any mkdir: a symlinked .mind would let init create
        # cortex/dreams directories outside the project (auditor finding)
        if self.dir.is_symlink():
            raise ValueError("refusing: .mind is a symlink")
        for subdir in (CORTEX_DIR, DREAMS_DIR):
            if (self.dir / subdir).is_symlink():
                raise ValueError("refusing: .mind/%s is a symlink" % subdir)
        _secure_mkdirs(self.dir, self.root)
        _secure_mkdirs(self.dir / CORTEX_DIR, self.root)
        _secure_mkdirs(self.dir / DREAMS_DIR, self.root)
        _sweep_tmp_files(self.dir)
        _sync_portable_runtime(self.root)
        StorageManager(self.root, None).recover_restore()
        existing = (self.dir / GRAPH_FILE).exists()
        self.hippo = Hippocampus(self.dir / GRAPH_FILE)
        if not existing:
            self.hippo._save()
        self.cortex = Cortex(self.dir / CORTEX_DIR)
        self.dreamer = Dreamer(self.dir, self.hippo, self.cortex)
        self.active = Active(self.dir, self.hippo, self.cortex)
        self.pending_queue = PendingQueue(self.dir)
        self.lifecycle = LifecycleManager(self.root, self.hippo)
        self.lifecycle.recover()
        self.storage = StorageManager(self.root, self.hippo)
        written = self._refresh_exports()
        verb = "repaired and refreshed" if existing else "created"
        print("""%s mind memory in %s

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

manual commands (optional):  %s remember/recall/dream/status

export results: %s""" % (
            verb, self.dir, _invocation(self.root), ", ".join(written)))

    def _ensure(self):
        if self.dir.is_symlink():
            raise ValueError("refusing: .mind is a symlink")
        if not self.dir.exists():
            print("no mind memory here. run: %s init"
                  % _invocation(self.root), file=sys.stderr)
            sys.exit(1)
        _sweep_tmp_files(self.dir)
        _sync_portable_runtime(self.root)
        StorageManager(self.root, None).recover_restore()
        self.hippo = Hippocampus(self.dir / GRAPH_FILE)
        self.cortex = Cortex(self.dir / CORTEX_DIR)
        self.dreamer = Dreamer(self.dir, self.hippo, self.cortex)
        self.active = Active(self.dir, self.hippo, self.cortex)
        self.pending_queue = PendingQueue(self.dir)
        self.lifecycle = LifecycleManager(self.root, self.hippo)
        self.lifecycle.recover()
        self.storage = StorageManager(self.root, self.hippo)

    def _ensure_user(self, create=False):
        configured = os.environ.get("MIND_USER_HOME")
        user_dir = (
            Path(configured).expanduser().resolve()
            if configured else (Path.home() / MIND_DIR).resolve())
        if user_dir.is_symlink():
            raise ValueError("refusing: user memory directory is a symlink")
        if not user_dir.exists():
            if not create:
                return None
            _secure_mkdirs(user_dir, user_dir.parent)
        if create:
            _secure_mkdirs(user_dir / CORTEX_DIR, user_dir.parent)
            _secure_mkdirs(user_dir / DREAMS_DIR, user_dir.parent)
        self.user_dir = user_dir
        self.user_hippo = Hippocampus(user_dir / GRAPH_FILE)
        if create and not (user_dir / GRAPH_FILE).exists():
            self.user_hippo._save()
        return self.user_hippo

    def _refresh_exports(self):
        """Publish derived files from the newest graph under the graph lock."""
        with self.hippo._transaction():
            try:
                self.active.generate(self.root)
            except (OSError, ValueError, UnicodeError) as e:
                return ["ACTIVE.md (skipped: unsafe or unwritable: %s)"
                        % _display_text(e, 120)]
            try:
                return self.active.export_to_agents(self.root)
            except (OSError, ValueError, UnicodeError) as e:
                return ["agent export (skipped: unsafe or unwritable: %s)"
                        % _display_text(e, 120)]

    @staticmethod
    def _export_summary(written):
        skipped = [item for item in written if "(skipped:" in item]
        return ("%d updated%s" % (
            len(written) - len(skipped),
            "; %d skipped (%s)" % (
                len(skipped), ", ".join(_display_text(s, 120)
                                        for s in skipped))
            if skipped else ""))

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
        token = None
        try:
            token = _scheduler_claim(self.dir)
            if token is None:
                return False
            memo, _ = self.dreamer.dream(dry_run=False)
            _scheduler_complete(self.dir, token)
            print("  🌙 auto-dream: memory consolidated (%s)"
                  % (memo or "journal skipped"))
            return True
        except Exception as e:                       # noqa: BLE001
            # maintenance must never break the write it rode on
            if token is not None:
                try:
                    _scheduler_release(self.dir, token)
                except Exception:
                    pass
            print("  (auto-dream skipped: %s)" % e, file=sys.stderr)
            return False

    def remember(self, text, metadata=None, confidence=1.0):
        self._ensure()
        metadata = dict(metadata or {})
        policy = PolicyEngine.classify(
            text,
            source_trust=metadata.get("source_trust", "user"),
            explicit=True)
        if policy["decision"] == "reject":
            raise ValueError("memory rejected: %s" % policy["reason"])
        scope = metadata.get("scope", "project")
        target = self.hippo
        if scope == "user":
            target = self._ensure_user(create=True)
            metadata["scope"] = "user"
        nid = target.remember(
            policy["text"], confidence=confidence, metadata=metadata)
        if scope == "user":
            print("remembered in user memory: %s" % _display_text(
                target.nodes[nid]["text"]))
            print("  (node user:%s, total user nodes: %d)" % (
                nid, len(target.nodes)))
            return
        self._auto_dream()
        written = self._refresh_exports()
        print("remembered: %s" % _display_text(self.hippo.nodes[nid]["text"]))
        print("  (node %s, total nodes: %d)" % (
            _display_text(nid, 128), len(self.hippo.nodes)))
        print("  export: %s" % self._export_summary(written))

    def remember_many(self, records):
        self._ensure()
        for record in records:
            text = record if isinstance(record, str) else record.get("text")
            trust = (
                record.get("source_trust", "user")
                if isinstance(record, dict) else "user")
            policy = PolicyEngine.classify(
                text, source_trust=trust, explicit=True)
            if policy["decision"] == "reject":
                raise ValueError(
                    "batch memory rejected: %s" % policy["reason"])
            if isinstance(record, dict) and record.get(
                    "scope", "project") != "project":
                raise ValueError(
                    "bulk ingest currently accepts project scope only")
        node_ids = self.hippo.remember_many(records)
        self._auto_dream()
        written = self._refresh_exports()
        print("remembered batch: %d records (%d total nodes)" % (
            len(node_ids), len(self.hippo.nodes)))
        print("  export: %s" % self._export_summary(written))

    def capture(self, text, source_trust="user"):
        self._ensure()
        decision = PolicyEngine.classify(
            text, source_trust=source_trust, explicit=False)
        if decision["decision"] == "accept":
            metadata = {
                "source_trust": source_trust,
                "sensitivity": "internal",
                "scope": "project",
            }
            metadata.update(
                PolicyEngine.infer_metadata(decision["text"]))
            self.remember(decision["text"], metadata=metadata)
            return "accepted"
        if decision["decision"] == "quarantine":
            item = self.pending_queue.add(
                decision["text"], decision["reason"], source_trust)
            print("quarantined for review: %s (%s)" % (
                item["id"], item["reason"]))
            return "quarantined"
        print("capture rejected: %s" % decision["reason"])
        return "rejected"

    def pending(self):
        self._ensure()
        items = self.pending_queue.list()
        if not items:
            print("pending queue is empty")
            return
        print("pending captures: %d" % len(items))
        for item in items:
            print("  %s [%s] %s" % (
                _display_text(item["id"], 32),
                _display_text(item.get("source_trust", "?"), 20),
                _display_text(item["text"], 300)))
            print("    reason: %s" % _display_text(
                item.get("reason", "review required"), 160))

    def approve(self, item_id):
        self._ensure()
        item = self.pending_queue.pop(item_id)
        if item is None:
            raise ValueError("unknown pending id: %s" % item_id)
        self.remember(item["text"])
        print("approved pending capture: %s" % item_id)

    def reject(self, item_id):
        self._ensure()
        item = self.pending_queue.pop(item_id)
        if item is None:
            raise ValueError("unknown pending id: %s" % item_id)
        print("rejected pending capture: %s" % item_id)

    def context(self, as_json=False):
        self._ensure()
        nodes = sorted(
            ((node_id, node) for node_id, node in self.hippo.nodes.items()
             if self.hippo._valid_at(node)),
            key=lambda item: (
                item[1].get("weight", 0.0),
                item[1].get("access_count", 0),
                item[1].get("last_accessed", ""),
                item[0],
            ),
            reverse=True,
        )[:8]
        data = {
            "format": 1,
            "version": __version__,
            "project_root": ".",
            "memories": [
                {
                    "id": node_id,
                    "text": node["text"],
                    "weight": node.get("weight", 1.0),
                    "confidence": node.get("confidence", 1.0),
                }
                for node_id, node in nodes
            ],
            "cortex": [
                path.name for path in self.cortex.files()[:6]
            ],
            "pending": len(self.pending_queue.list()),
            "scheduler": _read_scheduler_state(self.dir),
        }
        user = self._ensure_user(create=False)
        data["user_memories"] = (
            [
                {
                    "id": "user:" + node_id,
                    "text": node["text"],
                    "weight": node.get("weight", 1.0),
                }
                for node_id, node in sorted(
                    user.nodes.items(),
                    key=lambda item: (
                        item[1].get("weight", 0.0), item[0]),
                    reverse=True)
                if user._valid_at(node)
            ][:8]
            if user is not None else []
        )
        if as_json:
            print(json.dumps(data, ensure_ascii=False, sort_keys=True))
        else:
            for item in data["memories"]:
                print("- %s" % _display_text(item["text"]))
        return data

    def suggest_user(self, as_json=False):
        """Suggest strong project facts for explicit user-tier promotion."""
        self._ensure()
        user = self._ensure_user(create=False)
        existing = set(user.nodes) if user is not None else set()
        candidates = []
        for node_id, node in self.hippo.nodes.items():
            if node_id in existing or not self.hippo._valid_at(node):
                continue
            if node.get("scope", "project") != "project":
                continue
            if node.get("sensitivity") in ("sensitive", "secret"):
                continue
            if node.get("source_trust") in ("repository", "untrusted"):
                continue
            policy = PolicyEngine.classify(
                node.get("text", ""),
                source_trust=node.get("source_trust", "user"),
                explicit=False,
            )
            if policy["decision"] != "accept":
                continue
            access = int(node.get("access_count", 0))
            memory_type = node.get("type", "semantic")
            pinned = bool(node.get("pinned", False))
            if not (
                    pinned or access >= 2 or
                    (access >= 1 and memory_type in (
                        "decision", "procedural"))):
                continue
            score = (
                access * 10
                + (8 if memory_type == "procedural" else 0)
                + (6 if memory_type == "decision" else 0)
                + (20 if pinned else 0)
                + int(float(node.get("confidence", 1.0)) * 5)
            )
            candidates.append({
                "id": node_id,
                "text": node["text"],
                "score": score,
                "reason": (
                    "pinned" if pinned else
                    "%d confirmed uses; %s memory" % (
                        access, memory_type)
                ),
                "promotion": {
                    "scope": "user",
                    "requires_explicit_write": True,
                },
            })
        candidates.sort(key=lambda item: (
            -item["score"], item["id"]))
        candidates = candidates[:20]
        result = {
            "format": 1,
            "suggestions": candidates,
            "copied": 0,
            "policy": (
                "suggestions never copy project memory; use an explicit "
                "user-tier write after reviewing the text"),
        }
        if as_json:
            print(json.dumps(
                result, ensure_ascii=False, sort_keys=True))
            return result
        if not candidates:
            print("no user-tier promotion suggestions")
            return result
        print("user-tier promotion suggestions (%d; nothing copied):"
              % len(candidates))
        for item in candidates:
            print("  %s [%s] %s" % (
                item["id"], item["reason"],
                _display_text(item["text"], 500)))
        print("review a suggestion, then write it explicitly with:")
        print('  %s remember --user "text"' % _invocation(self.root))
        return result

    def integrations(self, as_json=False):
        """Emit portable argv recipes for host lifecycle integrations."""
        invocation = _invocation(self.root)
        prefix = CommandEmbed._split_command(invocation) or [invocation]
        recipes = {
            "format": 1,
            "session_start": {
                "argv": prefix + ["context", "--json"],
                "purpose": "inject current project and user memory context",
            },
            "durable_capture": {
                "argv": prefix + ["capture", "<durable-project-fact>"],
                "purpose": "policy-gate one automatically extracted fact",
            },
            "pre_compaction": {
                "argv": prefix + ["remember", "--batch"],
                "stdin": (
                    "JSONL strings or typed objects containing only durable "
                    "facts extracted by the host before context compaction"),
            },
            "session_end": {
                "argv": prefix + ["dream"],
                "purpose": "run deterministic maintenance after the session",
            },
            "scheduled_backstop": {
                "argv": prefix + ["dream"],
                "purpose": (
                    "optional maintenance backstop; the internal scheduler "
                    "remains authoritative and lease-bounded"),
            },
            "protocol_server": {
                "argv": prefix + ["mcp"],
                "transport": "standard input and output",
            },
        }
        if as_json:
            print(json.dumps(
                recipes, ensure_ascii=False, sort_keys=True))
            return recipes
        print("mind integration recipes")
        for name in (
                "session_start", "durable_capture", "pre_compaction",
                "session_end", "scheduled_backstop", "protocol_server"):
            recipe = recipes[name]
            print("  %s: %s" % (
                name, json.dumps(
                    recipe["argv"], ensure_ascii=False)))
        print("use argv arrays directly; do not interpolate untrusted text "
              "through a shell")
        return recipes

    def forget(self, node_id, reason="user requested"):
        self._ensure()
        if not self.hippo.forget(node_id, reason):
            raise ValueError("unknown memory id: %s" % node_id)
        self._refresh_exports()
        print("forgotten from retrieval: %s" % node_id)

    def unlink(self, a, b):
        self._ensure()
        if not self.hippo.unlink(a, b):
            raise ValueError("link not found")
        self._refresh_exports()
        print("unlinked memories")

    def redact(self, node_id, reason):
        self._ensure()
        result = self.lifecycle.begin(
            "redact", node_id, reason=reason)
        self._refresh_exports()
        print("redacted %s across managed stores (%d replacements)" % (
            result["digest"][:12],
            result["rewritten_occurrences"]))

    def purge(self, node_id, confirm=False):
        self._ensure()
        inventory = self.lifecycle.inventory(node_id)
        print("purge inventory for %s:" % node_id)
        print("  payload variants: %d" % len(inventory["originals"]))
        print("  files with payload: %d" % len(inventory["files"]))
        print("  byte occurrences: %d" % inventory["occurrences"])
        for item in inventory["files"]:
            print("  - %s: %d occurrence(s)" % (
                _display_text(item["path"], 300),
                item["occurrences"]))
        if not confirm:
            print("dry run only; rerun with --confirm --all-traces")
            return inventory
        result = self.lifecycle.begin(
            "purge", node_id, reason="irreversible user-confirmed purge")
        self._refresh_exports()
        print("purged %s across all managed stores (%d replacements)" % (
            result["digest"][:12],
            result["rewritten_occurrences"]))
        return result

    def find_memory_ids(self, match):
        self._ensure()
        match = Hippocampus._validated_query(match, "purge match")
        return [
            node_id for node_id, node in self.hippo.nodes.items()
            if match in node.get("text", "")
        ]

    def backup(self, label=None):
        self._ensure()
        name, manifest = self.storage.backup(label)
        print("backup created: %s (%d files)" % (
            name, len(manifest["files"])))
        return name

    def checkpoint(self, label=None):
        return self.backup(label or "checkpoint")

    def restore(self, name, confirm=False):
        self._ensure()
        result = self.storage.restore(name, confirm=confirm)
        if not confirm:
            print("restore plan: %s contains %d files" % (
                name, result["files"]))
            print("dry run only; rerun with --confirm")
            return result
        self._ensure()
        self._refresh_exports()
        print("restored %s (%d files); pre-restore checkpoint: %s" % (
            name, result["files"], result["checkpoint"]))
        return result

    def compact(self, dry_run=False, keep_journal_days=365):
        self._ensure()
        result = self.storage.compact(
            dry_run=dry_run,
            keep_journal_days=keep_journal_days)
        print(json.dumps(result, indent=2, sort_keys=True))
        return result

    def doctor(self, run_bench=False, as_json=False):
        self._ensure()
        doctor = Doctor(self.root, self.hippo, self.active)
        result = doctor.run()
        if run_bench:
            result["bench"] = doctor.bench()
        if as_json:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return result
        print("mind doctor: %s" % ("PASS" if result["ok"] else "FAIL"))
        if not result["findings"]:
            print("  no integrity or operability findings")
        for finding in result["findings"]:
            print("  %s %s: %s" % (
                finding["severity"].upper(),
                finding["code"], finding["message"]))
        if run_bench:
            bench = result["bench"]
            print("  personal recall: @1 %.3f | @5 %.3f (%d probes)" % (
                bench["recall_at_1"], bench["recall_at_5"],
                bench["probes"]))
        return result

    def growth(self, days=7, as_json=False):
        self._ensure()
        result = Growth(
            self.dir, self.hippo, self.cortex).digest(days=days)
        if as_json:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return result
        print("mind growth - last %d days" % days)
        print("  learned %d | confirmed %d | corrected %d | forgotten %d" % (
            result["facts_learned"], result["facts_confirmed"],
            result["facts_corrected"], result["facts_forgotten"]))
        print("  dreams %d | promoted %d | conflicts %d" % (
            result["dream_cycles"], result["promoted_clusters"],
            result["conflicts_flagged"]))
        print("  now %d current memories across %d cortex topics" % (
            result["current_memories"], result["cortex_topics"]))
        return result

    def link(self, a, b, relation="related"):
        self._ensure()
        result = self.hippo.link(a, b, relation)
        self._auto_dream()
        self._refresh_exports()
        print(_display_text(result))

    def recall(self, query, at=None, explain=False):
        self._ensure()
        results, latency, kinds = self.hippo.recall(query, at=at)
        user_results = []
        user_latency = 0.0
        user = self._ensure_user(create=False)
        if user is not None:
            user_results, user_latency, user_kinds = user.recall(
                query, at=at)
            seen = {node_id for node_id, _, _ in results}
            user_results = [
                (node_id, score, node)
                for node_id, score, node in user_results
                if node_id not in seen
            ]
            if user_results:
                combined = [
                    ("project", node_id, score * 1.05, node)
                    for node_id, score, node in results
                ]
                combined.extend(
                    ("user", node_id, score, node)
                    for node_id, score, node in user_results)
                combined.sort(key=lambda item: (
                    -item[2], 0 if item[0] == "project" else 1,
                    item[1]))
                combined = combined[:RECALL_TOP_K]
                results = [
                    (("user:" + node_id) if tier == "user" else node_id,
                     score, node)
                    for tier, node_id, score, node in combined
                ]
                kinds = {
                    (("user:" + node_id) if tier == "user" else node_id):
                    "%s/%s" % (
                        tier,
                        user_kinds.get(node_id, "trace")
                        if tier == "user" else kinds.get(
                            node_id, "trace"))
                    for tier, node_id, _, _ in combined
                }
        latency += user_latency
        if not results:
            print("no results for \"%s\"%s (empty graph or no match)"
                  % (_display_text(query), " at %s" % _display_text(
                      at[:10]) if at else ""))
            return
        when = " (as of %s)" % at[:10] if at else ""
        print("recall for \"%s\"%s — %d results [%.2f ms]\n"
              % (_display_text(query), _display_text(when),
                 len(results), latency))
        for i, (nid, score, n) in enumerate(results, 1):
            print("  %d. [%.3f] (%s) %s" % (
                i, score, _display_text(kinds.get(nid, "trace"), 40),
                _display_text(n["text"])))
            print("     (confidence %.1f, recalled %dx, weight %.2f, id %s)"
                  % (n.get("confidence", 1), n.get("access_count", 0),
                     n["weight"], _display_text(nid, 128)))
            if explain:
                source = self.user_hippo if nid.startswith(
                    "user:") and self.user_hippo is not None else self.hippo
                lookup = nid[5:] if nid.startswith("user:") else nid
                receipt = source.last_recall_explain.get(
                    "results", {}).get(lookup, {})
                print("     explain: %s" % json.dumps(
                    receipt, ensure_ascii=False, sort_keys=True))
        if explain:
            reports = {"project": self.hippo.reranker.last_report}
            if self.user_hippo is not None:
                reports["user"] = self.user_hippo.reranker.last_report
            print("\n  backend receipts: %s" % json.dumps(
                reports, ensure_ascii=False, sort_keys=True))
        # path-aware like the exported contract: agents copy this hint
        # literally, and a bare `mind.py` mis-fires outside the project
        # root — the same field-failure class _invocation() exists to kill
        # (auditor finding, 6.2.6)
        print("\n  (if a result actually answered you, reinforce it:"
              " %s confirm <id>)" % _invocation(self.root))

    def confirm(self, node_ids):
        self._ensure()
        unique_ids = list(dict.fromkeys(node_ids))
        project_ids = [
            node_id for node_id in unique_ids
            if not node_id.startswith("user:")]
        user_ids = [
            node_id[5:] for node_id in unique_ids
            if node_id.startswith("user:")]
        changed = self.hippo.bump(project_ids)
        user = self._ensure_user(create=False)
        user_changed = user.bump(user_ids) if user is not None else False
        changed = changed or user_changed
        known = [nid for nid in project_ids if nid in self.hippo.nodes]
        known.extend(
            "user:" + nid for nid in user_ids
            if user is not None and nid in user.nodes)
        unknown = [nid for nid in unique_ids if nid not in known]
        if changed and known:
            if project_ids:
                self._auto_dream()
                self._refresh_exports()
            print("reinforced %d memor%s — stability +%d days each, edges "
                  "restrengthened" % (len(known), "y" if len(known) == 1 else "ies",
                                      int(STABILITY_PER_ACCESS)))
        for nid in unknown:
            print("unknown id: %s (get ids from `recall` output)"
                  % _display_text(nid, 128),
                  file=sys.stderr)
        if not known:
            sys.exit(1)

    def correct(self, old_hint, new_text):
        self._ensure()
        old = self.hippo.correct(old_hint, new_text)
        if old is None:
            print("no memory matched \"%s\" — nothing corrected."
                  % _display_text(old_hint))
            return
        if self.hippo._clean_text(new_text) == old:
            print("already current — nothing changed.")
            return
        self._auto_dream()
        self._refresh_exports()
        print("reconsolidated:")
        print("  was: %s" % _display_text(old))
        print("  now: %s" % _display_text(
            self.hippo._clean_text(new_text)))
        print("  (old fact CLOSED, not erased — `why` and `--at` can still reach it)")

    def why(self, nid):
        """Bounded provenance answer: origin, validity, and latest events."""
        self._ensure()
        n = self.hippo.nodes.get(nid)
        if n is None:
            # the fact may have been pruned from the graph — the journal
            # is permanent, so provenance must still answer (auditor
            # finding: the docs promised lineage the command refused)
            events = self.hippo.journal_entries(nid)
            if not events:
                print("unknown id: %s (get ids from `recall` or `entity`)"
                      % _display_text(nid, 128), file=sys.stderr)
                sys.exit(1)
            count = getattr(events, "total_count", len(events))
            trunc = "" if count <= 8 else "; last 8 shown"
            print("memory %s" % _display_text(nid, 128))
            print("  status:     PRUNED from the graph — journal lineage "
                  "(%d events%s):" % (count, trunc))
            for e in events[-8:]:
                extra = ""
                for f in ("text", "old_text", "new_text"):
                    if e.get(f):
                        extra = "  %s" % _display_text(e[f], 70)
                        break
                print("    %s %s by=%s%s" % (
                    _display_text(e.get("ts", "?"), 19),
                    _display_text(e.get("op", "?"), 40),
                    _display_text(e.get("by", "?"), 80), extra))
            return
        origin = n.get("origin", {})
        vt = n.get("valid_to")
        print("memory %s" % _display_text(nid, 128))
        print("  text:       %s" % _display_text(n["text"]))
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
                  % (_display_text(h.get("text", "?")),
                     _display_text(h.get("replaced", "?"), 19)))
        rels = [(nbr, e) for nbr, e in self.hippo.edges.get(nid, {}).items()
                if e.get("relation") in ("supersedes", "superseded-by")]
        for nbr, e in rels:
            other = self.hippo.nodes.get(nbr, {})
            print("  %s: %s (%s)" % (
                _display_text(e["relation"], 60),
                _display_text(nbr, 128),
                _display_text(other.get("text", "?"), 60)))
        for nbr, edge in self.hippo.edges.get(nid, {}).items():
            if edge.get("relation") in ("supersedes", "superseded-by"):
                continue
            other = self.hippo.nodes.get(nbr, {})
            direction = " -> " if edge.get("directed") else " <-> "
            conflict = ""
            if edge.get("relation") == "possible-conflict":
                conflict = " [%s%s]" % (
                    edge.get("conflict_kind", "legacy"),
                    " %s.%s" % (
                        edge.get("conflict_entity"),
                        edge.get("conflict_attr"))
                    if edge.get("conflict_kind") == "slot" else "",
                )
            print("  relation:   %s%s%s%s (%s)" % (
                _display_text(edge.get("relation", "related"), 60),
                _display_text(conflict, 260),
                direction,
                _display_text(nbr, 128),
                _display_text(other.get("text", "?"), 60)))
        events = self.hippo.journal_entries(nid)
        if events:
            count = getattr(events, "total_count", len(events))
            trunc = ("" if count <= 8 else
                     "; last 8 shown — journal file retains more")
            print("  journal (%d events%s):" % (count, trunc))
            for e in events[-8:]:
                print("    %s %s%s" % (
                    _display_text(e.get("ts", "?"), 19),
                    _display_text(e.get("op", "?"), 40),
                    " by=%s" % _display_text(e.get("by"), 80)
                    if e.get("by") else ""))
        else:
            print("  journal:    (no entries — predates 6.0.0 or journal lost)")

    def entity(self, term):
        """Entity view: every fact — current and superseded — that
        mentions this (normalized) term, with validity intervals."""
        self._ensure()
        term = self.hippo._validated_query(term, "entity term")
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
            print("no indexable term in \"%s\"" % _display_text(term))
            return
        rows = []
        for nid, n in self.hippo.nodes.items():
            nkeys = set(n.get("keys", []))
            nstems = {stem(k) for k in nkeys}
            if wanted & nkeys or wanted & nstems:
                rows.append((n.get("valid_from", ""), nid, n))
        if not rows:
            print("no facts mention \"%s\"" % _display_text(term))
            return
        rows.sort()
        print("entity \"%s\" — %d fact(s):\n"
              % (_display_text(term), len(rows)))
        for _, nid, n in rows:
            vt = n.get("valid_to")
            span = ("%s -> now" % n.get("valid_from", "?")[:10] if vt is None
                    else "%s -> %s" % (n.get("valid_from", "?")[:10], vt[:10]))
            mark = "  " if vt is None else "✗ "
            origin = n.get("origin", {})
            arrow = (" -> superseded by %s" % n["superseded_by"]
                     if n.get("superseded_by") else "")
            print("  %s[%s] %s (id %s, by %s via %s)%s"
                  % (_display_text(mark, 4), _display_text(span, 40),
                     _display_text(n["text"]), _display_text(nid, 128),
                     _display_text(origin.get("by", "unknown"), 80),
                     _display_text(origin.get("via", "?"), 40),
                     _display_text(arrow, 180)))

    def dream(self, dry_run=False):
        self._ensure()
        memo, text = self.dreamer.dream(dry_run=dry_run)
        if dry_run:
            print(text)
            print("(dry run — nothing was written)")
            return
        self._refresh_exports()
        print("dream cycle complete. journal: %s" % memo)
        print("  (read it to see what was forgotten, promoted, or flagged)")

    def export(self):
        self._ensure()
        written = self._refresh_exports()
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
        active_path = self.dir / ACTIVE_FILE
        active_size = (active_path.stat().st_size
                       if active_path.exists() and not active_path.is_symlink()
                       else 0)
        scheduler = _read_scheduler_state(self.dir)
        pending = scheduler["pending"]
        journal_entries = self.hippo.journal_entries()
        journal_n = journal_entries.total_count
        journal_retained = len(journal_entries)
        storage = self.storage.report()
        journal_current_bytes = storage["journal_current"]["bytes"]
        journal_segment_bytes = storage["journal_segments"]["bytes"]
        journal_segment_count = storage["journal_segments"]["count"]
        signal_path = self.dir / SIGNALS_FILE
        signal_bytes = (
            signal_path.stat().st_size
            if signal_path.exists() and not signal_path.is_symlink()
            else 0)
        graph_bytes = (
            (self.dir / GRAPH_FILE).stat().st_size
            if (self.dir / GRAPH_FILE).exists() else 0)
        archive_bytes = sum(
            path.stat().st_size for path in self.dir.glob("archive*.md")
            if path.is_file() and not path.is_symlink())
        tmp_debris = sum(
            1 for path in self.dir.glob("*.tmp")
            if path.is_file() and not path.is_symlink())
        print("""=== mind memory health ===
path:            %s
nodes:           %d (%d currently true, %d superseded)
edges:           %d
avg weight:      %.3f
cortex files:    %d
working memory:  %d bytes (~%d estimated tokens)
pending signals: %d
journal events:  %d total (%d retained from the bounded read window)
journal storage: current %d B | %d segments / %d B
storage:         graph %d B | archive %d B | signals %d B
temporary files: %d stale candidates
version:         %s""" % (self.dir, n_nodes, n_valid, n_nodes - n_valid,
                          n_edges, avg_w, cortex_n,
                          active_size, active_size // 4, pending,
                          journal_n, journal_retained,
                          journal_current_bytes, journal_segment_count,
                          journal_segment_bytes,
                          graph_bytes, archive_bytes,
                          signal_bytes, tmp_debris, __version__))


class MCPServer:
    """Minimal stdio MCP server using newline-delimited JSON-RPC 2.0."""

    def __init__(self, project_root=None):
        self.root = Path(project_root or os.getcwd()).resolve()
        self.mind = Mind(self.root)
        self.initialized = False

    @staticmethod
    def _schema(properties=None, required=None):
        return {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        }

    @classmethod
    def tools(cls):
        string = {"type": "string"}
        boolean = {"type": "boolean"}
        array = {"type": "array", "items": string, "minItems": 1}
        return [
            {
                "name": "remember",
                "description": "Store one durable project fact.",
                "inputSchema": cls._schema(
                    {
                        "text": string,
                        "automatic": {
                            "type": "boolean",
                            "default": False,
                        },
                        "source_trust": {
                            "type": "string",
                            "enum": sorted(PolicyEngine.TRUST_LEVELS),
                        },
                        "type": {
                            "type": "string",
                            "enum": sorted(MEMORY_TYPES),
                        },
                        "scope": {
                            "type": "string",
                            "enum": sorted(MEMORY_SCOPES),
                        },
                        "sensitivity": {
                            "type": "string",
                            "enum": sorted(MEMORY_SENSITIVITY),
                        },
                        "authority": string,
                        "expires_at": string,
                        "pinned": boolean,
                        "entity": string,
                        "attr": string,
                    },
                    ["text"]),
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "openWorldHint": False,
                },
            },
            {
                "name": "recall",
                "description": "Recall project facts for a question.",
                "inputSchema": cls._schema(
                    {"query": string, "at": string}, ["query"]),
                "annotations": {
                    "readOnlyHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "confirm",
                "description": "Reinforce recalled memories that answered.",
                "inputSchema": cls._schema(
                    {"ids": array}, ["ids"]),
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "openWorldHint": False,
                },
            },
            {
                "name": "correct",
                "description": "Supersede an incorrect fact with a new fact.",
                "inputSchema": cls._schema(
                    {"old_hint": string, "new_text": string},
                    ["old_hint", "new_text"]),
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "link",
                "description": "Link two facts with a typed relation.",
                "inputSchema": cls._schema(
                    {"a": string, "b": string, "relation": string},
                    ["a", "b"]),
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "openWorldHint": False,
                },
            },
            {
                "name": "why",
                "description": "Explain one memory's provenance and history.",
                "inputSchema": cls._schema({"id": string}, ["id"]),
                "annotations": {
                    "readOnlyHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "entity",
                "description": "List current and historical facts for a term.",
                "inputSchema": cls._schema({"term": string}, ["term"]),
                "annotations": {
                    "readOnlyHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "dream",
                "description": "Run deterministic memory maintenance.",
                "inputSchema": cls._schema(
                    {"dry_run": boolean}),
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "status",
                "description": "Return memory and storage health.",
                "inputSchema": cls._schema(),
                "annotations": {
                    "readOnlyHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "context",
                "description": "Return structured hot project and user context.",
                "inputSchema": cls._schema(),
                "annotations": {
                    "readOnlyHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "suggest_user",
                "description": (
                    "Suggest reviewed project facts for explicit user-tier "
                    "promotion without copying them."),
                "inputSchema": cls._schema(),
                "annotations": {
                    "readOnlyHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "doctor",
                "description": "Run deterministic integrity and operability checks.",
                "inputSchema": cls._schema({"bench": boolean}),
                "annotations": {
                    "readOnlyHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "growth",
                "description": "Return the journal-grounded memory growth digest.",
                "inputSchema": cls._schema({
                    "days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 36500,
                    },
                }),
                "annotations": {
                    "readOnlyHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "forget",
                "description": (
                    "Tombstone one memory out of retrieval while retaining "
                    "its provenance."),
                "inputSchema": cls._schema(
                    {"id": string, "reason": string}, ["id"]),
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "unlink",
                "description": "Remove a relation without deleting either memory.",
                "inputSchema": cls._schema(
                    {"a": string, "b": string}, ["a", "b"]),
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "redact",
                "description": (
                    "Replace a memory payload across managed stores with a "
                    "digest receipt."),
                "inputSchema": cls._schema(
                    {"id": string, "reason": string}, ["id", "reason"]),
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "purge",
                "description": (
                    "Inventory or irreversibly remove all managed traces. "
                    "confirm=false is always a dry run."),
                "inputSchema": cls._schema(
                    {"id": string, "confirm": boolean}, ["id"]),
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "openWorldHint": False,
                },
            },
        ]

    @staticmethod
    def _response(request_id, result=None, error=None):
        response = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            response["error"] = error
        else:
            response["result"] = result if result is not None else {}
        return response

    @staticmethod
    def _error(code, message, data=None):
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return error

    def _capture(self, method, *args, **kwargs):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO
        output, error = StringIO(), StringIO()
        try:
            with redirect_stdout(output), redirect_stderr(error):
                if not self.mind.dir.exists():
                    self.mind.init()
                method(*args, **kwargs)
        except SystemExit as exc:
            raise ValueError(
                error.getvalue().strip() or
                "tool exited with status %s" % exc.code)
        text = output.getvalue().strip()
        warning = error.getvalue().strip()
        if warning:
            text = (text + "\n" if text else "") + warning
        return text

    def _call_tool(self, name, arguments):
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        if name == "remember":
            if arguments.get("automatic", False):
                return self._capture(
                    self.mind.capture, arguments.get("text"),
                    source_trust=arguments.get(
                        "source_trust", "user"))
            metadata = {
                key: arguments[key] for key in (
                    "type", "scope", "sensitivity", "authority",
                    "expires_at", "pinned", "entity", "attr",
                    "source_trust")
                if key in arguments
            }
            return self._capture(
                self.mind.remember, arguments.get("text"),
                metadata=metadata)
        if name == "recall":
            return self._capture(
                self.mind.recall, arguments.get("query"),
                at=arguments.get("at"))
        if name == "confirm":
            return self._capture(
                self.mind.confirm, arguments.get("ids"))
        if name == "correct":
            return self._capture(
                self.mind.correct, arguments.get("old_hint"),
                arguments.get("new_text"))
        if name == "link":
            return self._capture(
                self.mind.link, arguments.get("a"), arguments.get("b"),
                arguments.get("relation", "related"))
        if name == "why":
            return self._capture(
                self.mind.why, arguments.get("id"))
        if name == "entity":
            return self._capture(
                self.mind.entity, arguments.get("term"))
        if name == "dream":
            return self._capture(
                self.mind.dream,
                dry_run=bool(arguments.get("dry_run", False)))
        if name == "status":
            return self._capture(self.mind.status)
        if name == "context":
            return self._capture(
                self.mind.context, as_json=True)
        if name == "suggest_user":
            return self._capture(
                self.mind.suggest_user, as_json=True)
        if name == "doctor":
            return self._capture(
                self.mind.doctor,
                run_bench=bool(arguments.get("bench", False)),
                as_json=True)
        if name == "growth":
            days = arguments.get("days", 7)
            if not isinstance(days, int) or not (1 <= days <= 36500):
                raise ValueError("growth days must be between 1 and 36500")
            return self._capture(
                self.mind.growth, days=days, as_json=True)
        if name == "forget":
            return self._capture(
                self.mind.forget, arguments.get("id"),
                arguments.get("reason", "agent requested"))
        if name == "unlink":
            return self._capture(
                self.mind.unlink,
                arguments.get("a"), arguments.get("b"))
        if name == "redact":
            return self._capture(
                self.mind.redact,
                arguments.get("id"), arguments.get("reason"))
        if name == "purge":
            return self._capture(
                self.mind.purge, arguments.get("id"),
                confirm=bool(arguments.get("confirm", False)))
        raise KeyError(name)

    def handle(self, request):
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return self._response(
                request.get("id") if isinstance(request, dict) else None,
                error=self._error(-32600, "Invalid Request"))
        method = request.get("method")
        request_id = request.get("id")
        notification = "id" not in request
        if not isinstance(method, str):
            return None if notification else self._response(
                request_id, error=self._error(
                    -32600, "Invalid Request"))
        if method == "notifications/initialized":
            self.initialized = True
            return None
        if method == "notifications/cancelled":
            return None
        if method == "initialize":
            params = request.get("params", {})
            requested = (
                params.get("protocolVersion")
                if isinstance(params, dict) else None)
            supported = (
                requested if requested in (
                    MCP_PROTOCOL_VERSION, "2025-06-18")
                else MCP_PROTOCOL_VERSION)
            return self._response(request_id, {
                "protocolVersion": supported,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "mind",
                    "title": "mind local agent memory",
                    "version": __version__,
                },
                "instructions": (
                    "Use remember for durable project facts, recall before "
                    "claiming ignorance, and confirm useful hits."),
            })
        if method == "ping":
            return self._response(request_id, {})
        if not self.initialized:
            return None if notification else self._response(
                request_id, error=self._error(
                    -32002, "Server not initialized"))
        if method == "tools/list":
            return self._response(
                request_id, {"tools": self.tools()})
        if method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict) or not isinstance(
                    params.get("name"), str):
                return self._response(
                    request_id, error=self._error(
                        -32602, "Invalid tools/call parameters"))
            try:
                text = self._call_tool(
                    params["name"], params.get("arguments", {}))
            except KeyError:
                return self._response(
                    request_id, error=self._error(
                        -32602, "Unknown tool: %s" % params["name"]))
            except Exception as exc:
                return self._response(request_id, {
                    "content": [{
                        "type": "text",
                        "text": _display_text(exc, 1000),
                    }],
                    "isError": True,
                })
            return self._response(request_id, {
                "content": [{"type": "text", "text": text}],
                "structuredContent": {"text": text},
                "isError": False,
            })
        return None if notification else self._response(
            request_id, error=self._error(
                -32601, "Method not found: %s" % method))

    def run_stdio(self, stdin=None, stdout=None):
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for raw in stdin:
            if not raw.strip():
                continue
            try:
                request = json.loads(raw)
            except (json.JSONDecodeError, RecursionError) as exc:
                response = self._response(
                    None, error=self._error(
                        -32700, "Parse error", _display_text(exc, 200)))
            else:
                response = self.handle(request)
            if response is not None:
                stdout.write(json.dumps(
                    response, ensure_ascii=False,
                    separators=(",", ":")) + "\n")
                stdout.flush()
        return 0


USAGE_TEMPLATE = """mind — brain-like memory for any coding agent (v%s)

usage: %s <command> [args]

commands:
  init                    create .mind/ memory in this project
  remember "text"         add a memory
  remember --user "text"  add an explicit user-global memory
  remember --json         read one typed memory object from stdin
  remember --batch        read JSONL strings/objects from stdin atomically
  capture "text"          policy-gated automatic project-fact capture
  pending                 list quarantined automatic captures
  approve <id>            approve and remember a quarantined capture
  reject <id>             discard a quarantined capture
  context [--json]        emit hook/session context
  suggest-user [--json]   review strong facts for explicit user promotion
  integrations [--json]   emit portable lifecycle-hook argv recipes
  forget <id>             tombstone a memory out of retrieval
  unlink <a> <b>          remove a relation
  redact <id> --reason X  replace payloads with a digest and reason
  purge <id> --all-traces [--confirm]
                          inventory or irreversibly remove every payload trace
  backup [label]          create a verified plain-file snapshot
  checkpoint [label]      create a named pre-change snapshot
  restore <name> [--confirm]
                          verify or restore a snapshot
  compact [--dry-run] [--keep-journal-days N]
                          segment growth and collect stale temporary files
  merge BASE OURS THEIRS [--output PATH] [--graph-out PATH]
                          deterministically merge journal suffixes and replay
  doctor [--bench] [--json]
                          integrity, boundary, backend, and recall checks
  growth [--days N] [--json]
                          summarize learned and consolidated memory
  link "a" "b" [rel]      connect two memories
  recall "question"       spreading-activation recall (prints memory ids)
  recall "q" --at DATE    what was true then (bare date = end of that day)
  recall "q" --explain    include channel and backend receipts
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
  mcp                     serve MCP over stdin/stdout
"""


def _usage(project_root=None):
    return USAGE_TEMPLATE % (
        __version__, _invocation(project_root), AUTO_DREAM_SIGNALS)


def _source_identity():
    try:
        source = Path(__file__).resolve()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
    except (OSError, ValueError):
        digest = "unknown"
    commit = "unknown"
    try:
        root = Path(__file__).resolve().parent
        head = (root / ".git" / "HEAD").read_text("utf-8").strip()
        if head.startswith("ref: "):
            ref = root / ".git" / head[5:]
            commit = ref.read_text("utf-8").strip()
        elif re.fullmatch(r"[0-9a-fA-F]{40}", head):
            commit = head.lower()
    except (OSError, ValueError):
        pass
    return commit, digest


def _die(msg, code=2):
    print("error: %s" % msg, file=sys.stderr)
    sys.exit(code)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    project_root = os.getcwd()
    usage = _usage(project_root)
    invocation = _invocation(project_root)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage)
        return 0
    if argv[0] in ("-v", "--version", "version"):
        if len(argv) > 2 or (
                len(argv) == 2 and argv[1] != "--verbose"):
            _die("version accepts only --verbose")
        if "--verbose" in argv[1:]:
            commit, digest = _source_identity()
            print("%s commit=%s sha256=%s" % (
                __version__, commit, digest))
        else:
            print(__version__)
        return 0
    import difflib
    cmd = argv[0]
    COMMANDS = {"init", "remember", "link", "recall", "confirm", "correct",
                "why", "entity", "dream", "export", "status", "mcp",
                "capture", "pending", "approve", "reject", "context",
                "suggest-user", "integrations",
                "forget", "unlink", "redact", "purge", "backup",
                "checkpoint", "restore", "compact", "merge",
                "doctor", "growth"}
    if cmd not in COMMANDS:
        sug = difflib.get_close_matches(cmd, COMMANDS, n=1, cutoff=0.6)
        hint = " did you mean `%s`?" % sug[0] if sug else ""
        _die("unknown command: %s.%s\n\n%s" % (cmd, hint, usage))
    # reject unknown flags: a typo like `dream --dryrun` must never fall
    # through to the destructive default
    KNOWN_FLAGS = {
        "dream": {"--dry-run"},
        "recall": {"--at", "--explain", "--"},
        "capture": {"--trust"},
        "context": {"--json"},
        "suggest-user": {"--json"},
        "integrations": {"--json"},
        "forget": {"--reason"},
        "redact": {"--reason"},
        "purge": {"--match", "--all-traces", "--confirm"},
        "restore": {"--confirm"},
        "compact": {"--dry-run", "--keep-journal-days"},
        "merge": {"--output", "--graph-out"},
        "doctor": {"--bench", "--json"},
        "growth": {"--days", "--json"},
    }
    if cmd in KNOWN_FLAGS:
        # strict scan ONLY for commands with flags: a typo like `dream
        # --dryrun` must never fall through to the destructive default —
        # but free-text commands must accept text that merely starts
        # with dashes (auditor finding)
        skip_value = False
        end_options = False
        for a in argv[1:]:
            if a == "--":
                end_options = True
                continue
            if end_options:
                continue
            if skip_value:
                skip_value = False
                continue
            if a == "--at":
                skip_value = True
            if a == "--trust":
                skip_value = True
            if a in ("--reason", "--match"):
                skip_value = True
            if a == "--keep-journal-days":
                skip_value = True
            if a in ("--output", "--graph-out"):
                skip_value = True
            if a == "--days":
                skip_value = True
            if a.startswith("--") and a not in KNOWN_FLAGS[cmd]:
                _die("unknown option %s for `%s` (allowed: %s)" % (
                    a, cmd, ", ".join(sorted(KNOWN_FLAGS[cmd]))))
    if cmd == "mcp":
        if len(argv) != 1:
            _die("usage: %s mcp" % invocation)
        return MCPServer(project_root).run_stdio()
    if cmd == "merge":
        args = argv[1:]
        output = None
        graph_out = None
        for flag in ("--output", "--graph-out"):
            if args.count(flag) > 1:
                _die("merge accepts at most one %s" % flag)
            if flag in args:
                index = args.index(flag)
                if index + 1 >= len(args):
                    _die("%s requires a path" % flag)
                value = args[index + 1]
                args = args[:index] + args[index + 2:]
                if flag == "--output":
                    output = value
                else:
                    graph_out = value
        if len(args) != 3:
            _die("usage: %s merge BASE OURS THEIRS "
                 "[--output PATH] [--graph-out PATH]" % invocation)
        result = JournalMerger.merge_files(
            args[0], args[1], args[2],
            output=output, graph_out=graph_out)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    m = Mind()
    try:
        if cmd == "init":
            if len(argv) != 1:
                _die("usage: %s init" % invocation)
            m.init()
        elif cmd == "remember":
            if argv[1:2] == ["--user"]:
                text = " ".join(argv[2:]).strip()
                if not text:
                    _die('usage: %s remember --user "text"'
                         % invocation)
                m.remember(text, metadata={"scope": "user"})
            elif argv[1:] == ["--json"]:
                try:
                    record = json.load(sys.stdin)
                except json.JSONDecodeError as exc:
                    _die("invalid remember JSON: %s" % exc)
                if not isinstance(record, dict) or not isinstance(
                        record.get("text"), str):
                    _die("remember --json requires an object with text")
                metadata = {
                    key: record[key] for key in (
                        "type", "scope", "authority", "source_trust",
                        "sensitivity", "expires_at", "pinned",
                        "entity", "attr")
                    if key in record
                }
                m.remember(
                    record["text"], metadata=metadata,
                    confidence=record.get("confidence", 1.0))
            elif argv[1:] == ["--batch"]:
                records = []
                for line_number, line in enumerate(sys.stdin, 1):
                    if not line.strip():
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        _die("invalid JSONL record on line %d: %s" % (
                            line_number, exc))
                if not records:
                    _die("remember --batch requires at least one JSONL record")
                m.remember_many(records)
            else:
                text = " ".join(argv[1:]).strip()
                if not text:
                    _die('usage: %s remember "text" (text must not be empty)'
                         % invocation)
                m.remember(text)
        elif cmd == "capture":
            args = argv[1:]
            trust = "user"
            if args.count("--trust") > 1:
                _die("capture accepts at most one --trust value")
            if "--trust" in args:
                index = args.index("--trust")
                if index + 1 >= len(args):
                    _die("--trust needs user|repository|tool|untrusted")
                trust = args[index + 1]
                args = args[:index] + args[index + 2:]
            text = " ".join(args).strip()
            if not text:
                _die('usage: %s capture "text" [--trust LEVEL]'
                     % invocation)
            m.capture(text, source_trust=trust)
        elif cmd == "pending":
            if len(argv) != 1:
                _die("usage: %s pending" % invocation)
            m.pending()
        elif cmd == "approve":
            if len(argv) != 2:
                _die("usage: %s approve <pending-id>" % invocation)
            m.approve(argv[1])
        elif cmd == "reject":
            if len(argv) != 2:
                _die("usage: %s reject <pending-id>" % invocation)
            m.reject(argv[1])
        elif cmd == "context":
            if len(argv) > 2 or (
                    len(argv) == 2 and argv[1] != "--json"):
                _die("usage: %s context [--json]" % invocation)
            m.context(as_json="--json" in argv[1:])
        elif cmd == "suggest-user":
            if len(argv) > 2 or (
                    len(argv) == 2 and argv[1] != "--json"):
                _die("usage: %s suggest-user [--json]" % invocation)
            m.suggest_user(as_json="--json" in argv[1:])
        elif cmd == "integrations":
            if len(argv) > 2 or (
                    len(argv) == 2 and argv[1] != "--json"):
                _die("usage: %s integrations [--json]" % invocation)
            m.integrations(as_json="--json" in argv[1:])
        elif cmd == "forget":
            args = argv[1:]
            reason = "user requested"
            if "--reason" in args:
                index = args.index("--reason")
                if index + 1 >= len(args):
                    _die("--reason requires text")
                reason = args[index + 1]
                args = args[:index] + args[index + 2:]
            if len(args) != 1:
                _die("usage: %s forget <id> [--reason TEXT]"
                     % invocation)
            m.forget(args[0], reason)
        elif cmd == "unlink":
            if len(argv) != 3:
                _die("usage: %s unlink <id-or-text> <id-or-text>"
                     % invocation)
            m.unlink(argv[1], argv[2])
        elif cmd == "redact":
            args = argv[1:]
            if args.count("--reason") != 1:
                _die("redact requires exactly one --reason")
            index = args.index("--reason")
            if index + 1 >= len(args):
                _die("--reason requires text")
            reason = args[index + 1]
            args = args[:index] + args[index + 2:]
            if len(args) != 1:
                _die("usage: %s redact <id> --reason TEXT"
                     % invocation)
            m.redact(args[0], reason)
        elif cmd == "purge":
            args = argv[1:]
            if "--all-traces" not in args:
                _die("purge requires --all-traces")
            args.remove("--all-traces")
            confirm = "--confirm" in args
            if confirm:
                args.remove("--confirm")
            if "--match" in args:
                index = args.index("--match")
                if index + 1 >= len(args):
                    _die("--match requires text")
                match = args[index + 1]
                args = args[:index] + args[index + 2:]
                if args:
                    _die("purge accepts either an id or --match")
                node_ids = m.find_memory_ids(match)
                if not node_ids:
                    raise ValueError("no memories match the purge text")
            else:
                if len(args) != 1:
                    _die("usage: %s purge <id>|--match TEXT "
                         "--all-traces [--confirm]" % invocation)
                node_ids = args
            if confirm and len(node_ids) > 1:
                print("confirmed purge matches %d memories"
                      % len(node_ids))
            for node_id in list(node_ids):
                m.purge(node_id, confirm=confirm)
        elif cmd == "backup":
            if len(argv) > 2:
                _die("usage: %s backup [label]" % invocation)
            m.backup(argv[1] if len(argv) == 2 else None)
        elif cmd == "checkpoint":
            if len(argv) > 2:
                _die("usage: %s checkpoint [label]" % invocation)
            m.checkpoint(argv[1] if len(argv) == 2 else None)
        elif cmd == "restore":
            args = argv[1:]
            confirm = "--confirm" in args
            if confirm:
                args.remove("--confirm")
            if len(args) != 1:
                _die("usage: %s restore <name> [--confirm]"
                     % invocation)
            m.restore(args[0], confirm=confirm)
        elif cmd == "compact":
            args = argv[1:]
            dry_run = "--dry-run" in args
            if dry_run:
                args.remove("--dry-run")
            keep_days = 365
            if "--keep-journal-days" in args:
                index = args.index("--keep-journal-days")
                if index + 1 >= len(args):
                    _die("--keep-journal-days requires a positive integer")
                try:
                    keep_days = int(args[index + 1])
                except ValueError:
                    _die("--keep-journal-days requires a positive integer")
                if keep_days <= 0:
                    _die("--keep-journal-days requires a positive integer")
                args = args[:index] + args[index + 2:]
            if args:
                _die("usage: %s compact [--dry-run] "
                     "[--keep-journal-days N]" % invocation)
            m.compact(dry_run=dry_run, keep_journal_days=keep_days)
        elif cmd == "link":
            if len(argv) not in (3, 4):
                _die('usage: %s link "a" "b" ["relation"]' % invocation)
            m.link(argv[1], argv[2], argv[3] if len(argv) > 3 else "related")
        elif cmd == "recall":
            args = argv[1:]
            at = None
            explain = "--explain" in args
            if explain:
                args.remove("--explain")
            literal = []
            if "--" in args:
                sentinel = args.index("--")
                literal = args[sentinel + 1:]
                args = args[:sentinel]
            if args.count("--at") > 1:
                _die("`recall` accepts at most one --at value")
            if "--at" in args:
                i = args.index("--at")
                if i + 1 >= len(args):
                    _die("--at needs a date: recall \"q\" --at YYYY-MM-DD")
                at = args[i + 1]
                args = args[:i] + args[i + 2:]
                try:
                    parsed = datetime.fromisoformat(at)
                except ValueError:
                    _die("invalid --at date %r (use YYYY-MM-DD)" % at)
                # normalize BEFORE the lexicographic compare: compact
                # (20260101) and tz-aware forms parse fine on 3.11+ but
                # compare wrong against dashed naive stamps — '-' < '0'
                # made every same-year fact look "valid" at a past compact
                # date (auditor finding, 6.2.9)
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone().replace(tzinfo=None)
                if len(at) <= 10 and parsed == datetime(
                        parsed.year, parsed.month, parsed.day):
                    at = parsed.date().isoformat() + "T23:59:59"
                else:                      # bare date → inclusive end of day
                    at = parsed.isoformat()
            q = " ".join(args + literal).strip()
            if not q:
                _die('usage: %s recall "question" [--at YYYY-MM-DD]'
                     % invocation)
            m.recall(q, at=at, explain=explain)
        elif cmd == "why":
            if len(argv) != 2 or not argv[1].strip():
                _die('usage: %s why <id> (ids come from recall/entity output)'
                     % invocation)
            m.why(argv[1].strip())
        elif cmd == "entity":
            term = " ".join(argv[1:]).strip()
            if not term:
                _die('usage: %s entity "term"' % invocation)
            m.entity(term)
        elif cmd == "confirm":
            if len(argv) < 2:
                _die('usage: %s confirm <id> [<id>...] '
                     '(ids come from recall output)' % invocation)
            m.confirm(argv[1:])
        elif cmd == "correct":
            if len(argv) != 3 or not argv[1].strip() or not argv[2].strip():
                _die('usage: %s correct "old text hint" "corrected fact" '
                     '(neither may be empty)' % invocation)
            m.correct(argv[1], argv[2])
        elif cmd == "dream":
            if len(argv) > 2 or (len(argv) == 2 and argv[1] != "--dry-run"):
                _die("usage: %s dream [--dry-run]" % invocation)
            m.dream(dry_run="--dry-run" in argv[1:])
        elif cmd == "export":
            if len(argv) != 1:
                _die("usage: %s export" % invocation)
            m.export()
        elif cmd == "doctor":
            args = argv[1:]
            run_bench = "--bench" in args
            as_json = "--json" in args
            args = [
                value for value in args
                if value not in ("--bench", "--json")]
            if args:
                _die("usage: %s doctor [--bench] [--json]"
                     % invocation)
            result = m.doctor(
                run_bench=run_bench, as_json=as_json)
            if not result["ok"]:
                return 1
        elif cmd == "growth":
            args = argv[1:]
            as_json = "--json" in args
            if as_json:
                args.remove("--json")
            days = 7
            if "--days" in args:
                index = args.index("--days")
                if index + 1 >= len(args):
                    _die("--days requires a positive integer")
                try:
                    days = int(args[index + 1])
                except ValueError:
                    _die("--days requires a positive integer")
                if days <= 0:
                    _die("--days requires a positive integer")
                args = args[:index] + args[index + 2:]
            if args:
                _die("usage: %s growth [--days N] [--json]"
                     % invocation)
            m.growth(days=days, as_json=as_json)
        elif cmd == "status":
            if len(argv) != 1:
                _die("usage: %s status" % invocation)
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
