class LifecycleManager:
    """Crash-resumable cross-store redact and purge coordinator."""

    def __init__(self, root, hippo):
        self.root = Path(root).resolve()
        self.dir = self.root / MIND_DIR
        self.hippo = hippo
        self.outbox = self.dir / LIFECYCLE_OUTBOX_FILE

    def _managed_files(self):
        files = []
        excluded_paths = {
            (self.dir / GRAPH_FILE).resolve(),
            (self.dir / RUNTIME_FILE).resolve(),
            self.outbox.resolve(),
        }
        if self.dir.is_dir() and not self.dir.is_symlink():
            for base, directories, names in os.walk(str(self.dir)):
                directories[:] = [
                    name for name in directories
                    if not (Path(base) / name).is_symlink()
                ]
                for name in names:
                    path = Path(base) / name
                    if (path.resolve() in excluded_paths
                            or name.endswith(".lock")
                            or path.is_symlink() or not path.is_file()):
                        continue
                    files.append(path)
                    if len(files) >= 20_000:
                        break
                if len(files) >= 20_000:
                    break
        for target in Active.TARGETS:
            path = self.root / target
            if path.is_file() and not path.is_symlink():
                files.append(path)
        return sorted(set(files))

    def _relative(self, path):
        return str(Path(path).resolve().relative_to(self.root))

    def inventory(self, node_id):
        node = self.hippo.nodes.get(node_id)
        if node is None:
            raise ValueError("unknown memory id: %s" % node_id)
        originals = [node.get("text", "")]
        originals.extend(
            item.get("text", "")
            for item in node.get("history", [])
            if isinstance(item, dict)
        )
        originals = list(dict.fromkeys(
            text for text in originals if text))
        needle_bytes = [text.encode("utf-8") for text in originals]
        files = []
        total = 0
        for path in self._managed_files():
            try:
                payload = _read_bytes_bounded(
                    path, MAX_LIFECYCLE_FILE_BYTES, self.root)
            except (OSError, ValueError):
                continue
            count = sum(payload.count(needle) for needle in needle_bytes)
            if count:
                files.append({
                    "path": self._relative(path),
                    "occurrences": count,
                    "bytes": len(payload),
                })
                total += count
        return {
            "id": node_id,
            "originals": originals,
            "files": files,
            "occurrences": total,
        }

    def _write_outbox(self, state):
        _atomic_write(
            self.outbox,
            json.dumps(state, ensure_ascii=False, indent=2),
            boundary=self.dir)

    def _read_outbox(self):
        if not self.outbox.exists() or self.outbox.is_symlink():
            return None
        data = json.loads(_read_text_retry(
            self.outbox, max_bytes=MAX_AUX_BYTES,
            boundary=self.dir))
        if not isinstance(data, dict):
            raise ValueError("invalid lifecycle outbox")
        return data

    def _remove_outbox(self):
        if self.outbox.exists() and not self.outbox.is_symlink():
            self.outbox.unlink()

    def _rewrite_path(self, relative, needles, replacement):
        path = (self.root / relative).resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            raise UnsafePathError("lifecycle path escapes project")
        if not path.exists() or path.is_symlink() or not path.is_file():
            return 0
        payload = _read_bytes_bounded(
            path, MAX_LIFECYCLE_FILE_BYTES, self.root)
        changed = 0
        for needle in needles:
            count = payload.count(needle)
            if count:
                payload = payload.replace(needle, replacement)
                changed += count
        if changed:
            _atomic_write(path, payload, boundary=self.root)
        return changed

    def _refresh_backup_manifests(self):
        backups = self.dir / BACKUPS_DIR
        if not backups.is_dir() or backups.is_symlink():
            return 0
        refreshed = 0
        for directory in sorted(backups.iterdir()):
            manifest_path = directory / "manifest.json"
            if (directory.is_symlink() or not directory.is_dir()
                    or manifest_path.is_symlink()
                    or not manifest_path.is_file()):
                continue
            manifest = json.loads(_read_text_retry(
                manifest_path, max_bytes=MAX_AUX_BYTES,
                boundary=self.dir))
            entries = manifest.get("files")
            if not isinstance(entries, list):
                raise ValueError("invalid backup manifest")
            for entry in entries:
                relative = entry.get("path")
                if not isinstance(relative, str):
                    raise ValueError("invalid backup path")
                target = (directory / relative).resolve()
                try:
                    target.relative_to(directory.resolve())
                except ValueError:
                    raise UnsafePathError(
                        "backup path escapes snapshot")
                payload = _read_bytes_bounded(
                    target, MAX_LIFECYCLE_FILE_BYTES, self.dir)
                entry["bytes"] = len(payload)
                entry["sha256"] = hashlib.sha256(
                    payload).hexdigest()
            manifest["privacy_rewritten"] = _now().isoformat()
            _atomic_write(
                manifest_path,
                json.dumps(manifest, indent=2, sort_keys=True),
                boundary=self.dir,
            )
            refreshed += 1
        return refreshed

    def begin(self, operation, node_id, reason="user requested"):
        if operation not in ("redact", "purge"):
            raise ValueError("unsupported lifecycle operation")
        inventory = self.inventory(node_id)
        digest = hashlib.sha256(
            inventory["originals"][0].encode("utf-8")).hexdigest()
        replacement = (
            "[REDACTED sha256:%s reason:%s]" % (
                digest, _display_text(reason, 80))
            if operation == "redact"
            else "[PURGED sha256:%s]" % digest
        )
        paths = [
            self._relative(path) for path in self._managed_files()]
        state = {
            "format": 1,
            "operation": operation,
            "node_id": node_id,
            "node_id_digest": hashlib.sha256(
                node_id.encode("utf-8")).hexdigest(),
            "reason": _display_text(reason, 160),
            "digest": digest,
            "originals": inventory["originals"],
            "replacement": replacement,
            "graph_done": False,
            "pending_paths": paths,
            "rewritten_occurrences": 0,
            "backup_manifests_done": False,
            "receipt_done": False,
        }
        self._write_outbox(state)
        return self.recover()

    def recover(self):
        state = self._read_outbox()
        if state is None:
            return None
        operation = state.get("operation")
        node_id = state.get("node_id")
        originals = state.get("originals")
        replacement = state.get("replacement")
        if not (
                operation in ("redact", "purge")
                and isinstance(node_id, str)
                and isinstance(originals, list)
                and all(isinstance(text, str) for text in originals)
                and isinstance(replacement, str)):
            raise ValueError("invalid lifecycle recovery state")
        if not state.get("graph_done"):
            with self.hippo._transaction():
                if operation == "redact":
                    self.hippo._redact_node(
                        node_id, replacement, state.get("reason", ""),
                        state.get("digest", ""))
                else:
                    self.hippo._purge_node(node_id)
                self.hippo._flush_transaction()
            state["graph_done"] = True
            self._write_outbox(state)
        needles = [text.encode("utf-8") for text in originals]
        if operation == "purge":
            needles.append(node_id.encode("utf-8"))
        replacement_bytes = replacement.encode("utf-8")
        while state.get("pending_paths"):
            relative = state["pending_paths"][0]
            state["rewritten_occurrences"] += self._rewrite_path(
                relative, needles, replacement_bytes)
            del state["pending_paths"][0]
            self._write_outbox(state)
        if not state.get("backup_manifests_done"):
            state["backup_manifests_refreshed"] = (
                self._refresh_backup_manifests())
            state["backup_manifests_done"] = True
            self._write_outbox(state)
        if not state.get("receipt_done"):
            self.hippo._journal_immediate(
                operation,
                target_digest=state.get("digest"),
                reason=state.get("reason"),
                rewritten=state.get("rewritten_occurrences", 0))
            state["receipt_done"] = True
            self._write_outbox(state)
        result = {
            "operation": operation,
            "digest": state.get("digest"),
            "rewritten_occurrences": state.get(
                "rewritten_occurrences", 0),
        }
        self._remove_outbox()
        return result


class StorageManager:
    """Bounded storage reporting, segmentation, backup, and restore."""

    def __init__(self, root, hippo):
        self.root = Path(root).resolve()
        self.dir = self.root / MIND_DIR
        self.hippo = hippo
        self.backups = self.dir / BACKUPS_DIR
        self.restore_outbox = self.dir / RESTORE_OUTBOX_FILE

    @staticmethod
    def _digest(payload):
        return hashlib.sha256(payload).hexdigest()

    def report(self):
        def size(path):
            try:
                return path.stat().st_size
            except OSError:
                return 0
        graph = size(self.dir / GRAPH_FILE)
        signals = size(self.dir / SIGNALS_FILE)
        journal_current = size(self.dir / JOURNAL_FILE)
        segment_paths = (
            list((self.dir / JOURNAL_DIR).glob("*.jsonl"))
            if (self.dir / JOURNAL_DIR).is_dir()
            and not (self.dir / JOURNAL_DIR).is_symlink()
            else []
        )
        journal_segments = sum(
            size(path) for path in segment_paths
            if path.is_file() and not path.is_symlink())
        journal = journal_current + journal_segments
        archive = sum(
            size(path) for path in self.dir.glob("archive*.md"))
        created = []
        if self.hippo is not None:
            for node in self.hippo.nodes.values():
                try:
                    parsed = datetime.fromisoformat(
                        node.get("created", ""))
                    if parsed.tzinfo is not None:
                        parsed = parsed.astimezone().replace(tzinfo=None)
                    created.append(parsed)
                except (TypeError, ValueError):
                    continue
        observed_days = (
            max(1.0 / 24.0, (_now() - min(created)).total_seconds() / 86400)
            if created else None)

        def bounded(bytes_used, budget):
            estimate = None
            if observed_days is not None and bytes_used > 0:
                rate = bytes_used / observed_days
                estimate = max(0.0, (budget - bytes_used) / rate)
            return {
                "bytes": bytes_used,
                "budget": budget,
                "utilization": bytes_used / budget,
                "estimated_days_to_boundary": estimate,
                "estimate_basis": (
                    "average bytes since earliest live memory"
                    if estimate is not None else None),
            }
        return {
            "observed_age_days": observed_days,
            "graph": bounded(graph, MAX_GRAPH_BYTES),
            "signals": bounded(signals, MAX_SIGNALS_BYTES),
            "active_archive": bounded(
                size(self.dir / "archive.md"), ARCHIVE_ROTATE_BYTES),
            "archive_total": {"bytes": archive},
            "journal_current": bounded(
                journal_current, MAX_AUX_BYTES),
            "journal_segments": {
                "bytes": journal_segments,
                "count": sum(
                    path.is_file() and not path.is_symlink()
                    for path in segment_paths),
            },
            "journal_total": {"bytes": journal},
            "temporary_files": sum(
                1 for path in self.dir.glob("*.tmp")
                if path.is_file() and not path.is_symlink()),
        }

    def _snapshot_files(self):
        files = []
        if not self.dir.is_dir() or self.dir.is_symlink():
            return files
        for base, directories, names in os.walk(str(self.dir)):
            directories[:] = [
                name for name in directories
                if name != BACKUPS_DIR
                and not (Path(base) / name).is_symlink()
            ]
            for name in names:
                path = Path(base) / name
                if (name.endswith(".lock")
                        or name == LIFECYCLE_OUTBOX_FILE
                        or name == RESTORE_OUTBOX_FILE
                        or path.is_symlink() or not path.is_file()):
                    continue
                files.append(path)
        return sorted(files)

    def backup(self, label=None):
        stamp = _now().strftime("%Y%m%dT%H%M%S")
        safe_label = re.sub(
            r"[^A-Za-z0-9._-]+", "-", label or "backup").strip("-")
        name = "%s-%s" % (stamp, safe_label or "backup")
        _secure_mkdirs(self.backups, self.dir)
        destination = self.backups / name
        for index in range(1_000):
            candidate = destination if index == 0 else \
                self.backups / ("%s-%03d" % (name, index))
            if not candidate.exists() and not candidate.is_symlink():
                destination = candidate
                break
        _secure_mkdirs(destination, self.dir)
        manifest = {
            "format": 1,
            "created": _now().isoformat(),
            "version": __version__,
            "files": [],
        }
        for source in self._snapshot_files():
            relative = source.relative_to(self.dir)
            payload = _read_bytes_bounded(
                source, MAX_LIFECYCLE_FILE_BYTES, self.dir)
            target = destination / relative
            _secure_mkdirs(target.parent, self.dir)
            _atomic_write(target, payload, boundary=self.dir)
            manifest["files"].append({
                "path": str(relative),
                "bytes": len(payload),
                "sha256": self._digest(payload),
            })
        _atomic_write(
            destination / "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True),
            boundary=self.dir)
        return destination.name, manifest

    def _load_backup(self, name):
        if not isinstance(name, str) or not re.fullmatch(
                r"[A-Za-z0-9._-]+", name):
            raise ValueError("invalid backup name")
        source = self.backups / name
        if source.is_symlink() or not source.is_dir():
            raise ValueError("backup not found: %s" % name)
        manifest = json.loads(_read_text_retry(
            source / "manifest.json",
            max_bytes=MAX_AUX_BYTES, boundary=self.dir))
        if not isinstance(manifest, dict) or not isinstance(
                manifest.get("files"), list):
            raise ValueError("invalid backup manifest")
        for entry in manifest["files"]:
            relative = entry.get("path")
            if not isinstance(relative, str):
                raise ValueError("invalid backup path")
            path = (source / relative).resolve()
            try:
                path.relative_to(source.resolve())
            except ValueError:
                raise UnsafePathError("backup path escapes snapshot")
            payload = _read_bytes_bounded(
                path, MAX_LIFECYCLE_FILE_BYTES, self.dir)
            if self._digest(payload) != entry.get("sha256"):
                raise ValueError(
                    "backup digest mismatch: %s" % relative)
        return source, manifest

    def restore(self, name, confirm=False):
        source, manifest = self._load_backup(name)
        desired = sorted(entry["path"] for entry in manifest["files"])
        desired_set = set(desired)
        current = {
            str(path.relative_to(self.dir))
            for path in self._snapshot_files()
        }
        deleted = sorted(current - desired_set)
        if not confirm:
            return {
                "name": name,
                "files": len(manifest["files"]),
                "delete_files": deleted,
                "confirmed": False,
            }
        checkpoint, _ = self.backup("pre-restore")
        state = {
            "format": 1,
            "name": name,
            "files": len(manifest["files"]),
            "checkpoint": checkpoint,
            "pending_writes": desired,
            "pending_deletes": deleted,
            "delete_files": len(deleted),
        }
        self._write_restore_outbox(state)
        return self.recover_restore()

    def _write_restore_outbox(self, state):
        _atomic_write(
            self.restore_outbox,
            json.dumps(state, indent=2, sort_keys=True),
            boundary=self.dir)

    def _read_restore_outbox(self):
        if not self.restore_outbox.exists():
            return None
        if self.restore_outbox.is_symlink():
            raise UnsafePathError("restore outbox is a symlink")
        state = json.loads(_read_text_retry(
            self.restore_outbox,
            max_bytes=MAX_AUX_BYTES,
            boundary=self.dir))
        if not isinstance(state, dict):
            raise ValueError("invalid restore recovery state")
        return state

    def _remove_restore_outbox(self):
        if self.restore_outbox.exists() and \
                not self.restore_outbox.is_symlink():
            self.restore_outbox.unlink()

    def _restore_write_path(self, source, relative):
        relative = Path(relative)
        payload = _read_bytes_bounded(
            source / relative,
            MAX_LIFECYCLE_FILE_BYTES, self.dir)
        target = Path(os.path.abspath(str(self.dir / relative)))
        try:
            target.relative_to(self.dir.resolve())
        except ValueError:
            raise UnsafePathError("restore target escapes memory root")
        _secure_mkdirs(target.parent, self.dir)
        _atomic_write(target, payload, boundary=self.dir)

    def _restore_delete_path(self, relative):
        target = Path(os.path.abspath(str(self.dir / relative)))
        try:
            target.relative_to(self.dir.resolve())
        except ValueError:
            raise UnsafePathError("restore deletion escapes memory root")
        if not target.exists():
            return
        info = os.lstat(str(target))
        if target.is_symlink() or not stat.S_ISREG(info.st_mode) \
                or info.st_nlink != 1:
            raise UnsafePathError(
                "restore refuses unsafe extra file: %s" % relative)
        target.unlink()

    def recover_restore(self):
        state = self._read_restore_outbox()
        if state is None:
            return None
        if state.get("format") != 1 or not isinstance(
                state.get("name"), str) or not isinstance(
                state.get("checkpoint"), str) or not isinstance(
                state.get("files"), int) or not isinstance(
                state.get("delete_files"), int) or not isinstance(
                state.get("pending_writes"), list) or not isinstance(
                state.get("pending_deletes"), list):
            raise ValueError("invalid restore recovery state")
        source, manifest = self._load_backup(state["name"])
        manifest_paths = {
            entry["path"] for entry in manifest["files"]
        }
        if any(
                not isinstance(path, str) or path not in manifest_paths
                for path in state["pending_writes"]):
            raise ValueError("invalid restore write plan")
        if any(
                not isinstance(path, str)
                for path in state["pending_deletes"]):
            raise ValueError("invalid restore delete plan")
        while state["pending_writes"]:
            relative = state["pending_writes"][0]
            self._restore_write_path(source, relative)
            del state["pending_writes"][0]
            self._write_restore_outbox(state)
        while state["pending_deletes"]:
            relative = state["pending_deletes"][0]
            self._restore_delete_path(relative)
            del state["pending_deletes"][0]
            self._write_restore_outbox(state)
        result = {
            "name": state["name"],
            "files": state["files"],
            "deleted_files": state["delete_files"],
            "confirmed": True,
            "checkpoint": state["checkpoint"],
        }
        self._remove_restore_outbox()
        return result

    def _journal_newest_timestamp(self):
        current = self.dir / JOURNAL_FILE
        if not current.exists() or current.is_symlink():
            return None
        try:
            tail = _read_tail_text(
                current, RECEIPT_TAIL_BYTES, self.dir)
        except (OSError, ValueError, UnicodeError):
            return None
        for line in reversed(tail.splitlines()):
            try:
                event = json.loads(line)
                timestamp = event.get("ts")
                parsed = datetime.fromisoformat(timestamp)
            except (json.JSONDecodeError, TypeError, ValueError,
                    RecursionError):
                continue
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        return None

    def segment_journal(self, force=False, older_than=None):
        with self.hippo._thread_lock:
            with self.hippo._graph_lock():
                return self._segment_journal_locked(
                    force=force, older_than=older_than)

    def _segment_journal_locked(self, force=False, older_than=None):
        current = self.dir / JOURNAL_FILE
        if not current.exists() or current.is_symlink():
            return None
        info = os.lstat(str(current))
        if info.st_size == 0:
            return None
        if not force:
            over_budget = info.st_size >= MAX_AUX_BYTES
            age_eligible = False
            if older_than is not None:
                newest = self._journal_newest_timestamp()
                age_eligible = (
                    newest is not None and newest < older_than)
            if not over_budget and not age_eligible:
                return None
        segment_dir = self.dir / JOURNAL_DIR
        _secure_mkdirs(segment_dir, self.dir)
        stamp = _now().strftime("%Y-%m")
        target = None
        for index in range(1_000_000):
            candidate = segment_dir / (
                "%s-%04d.jsonl" % (stamp, index))
            if not candidate.exists() and not candidate.is_symlink():
                target = candidate
                break
        if target is None:
            raise OSError("could not allocate journal segment")
        payload = _read_bytes_bounded(
            current, MAX_LIFECYCLE_FILE_BYTES, self.dir)
        digest = self._digest(payload)
        os.replace(str(current), str(target))
        self.hippo._journal_immediate(
            "journal-segment",
            segment=target.name,
            segment_sha256=digest,
            segment_bytes=len(payload))
        return {
            "segment": str(target.relative_to(self.dir)),
            "sha256": digest,
            "bytes": len(payload),
        }

    def compact(self, dry_run=False, keep_journal_days=365):
        report = self.report()
        cutoff = _now() - timedelta(days=keep_journal_days)
        newest = self._journal_newest_timestamp()
        actions = {
            "journal_segment": (
                report["journal_current"]["bytes"] >= MAX_AUX_BYTES
                or (newest is not None and newest < cutoff)),
            "journal_newest": (
                newest.isoformat() if newest is not None else None),
            "journal_cutoff": cutoff.isoformat(),
            "archive_rotation": (
                report["active_archive"]["bytes"]
                >= ARCHIVE_ROTATE_BYTES),
            "temporary_files": report["temporary_files"],
            "keep_journal_days": keep_journal_days,
            "dry_run": dry_run,
        }
        if dry_run:
            return actions
        if actions["journal_segment"]:
            actions["journal_result"] = self.segment_journal(
                older_than=cutoff)
        if actions["archive_rotation"]:
            with self.hippo._thread_lock:
                with self.hippo._graph_lock():
                    actions["archive_result"] = str(
                        self.hippo._rotate_archive(
                            self.dir / "archive.md",
                            str(_now().date())).relative_to(self.dir))
        actions["temporary_removed"] = _sweep_tmp_files(
            self.dir, min_age_seconds=0)
        return actions


class JournalMerger:
    """Deterministic three-way journal merge and graph replay."""

    @staticmethod
    def read(path):
        events = []
        with open(path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "invalid journal JSON at %s:%d: %s" % (
                            path, line_number, exc))
                if not isinstance(event, dict) or not isinstance(
                        event.get("op"), str):
                    raise ValueError(
                        "invalid journal event at %s:%d" % (
                            path, line_number))
                events.append(event)
        return events

    @staticmethod
    def event_id(event):
        existing = event.get("event_id")
        if isinstance(existing, str) and existing:
            return existing
        payload = dict(event)
        payload.pop("event_id", None)
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:24]

    @staticmethod
    def event_time(event):
        value = event.get("ts_utc_ns")
        if isinstance(value, int):
            return value
        timestamp = event.get("ts")
        if isinstance(timestamp, str):
            try:
                parsed = datetime.fromisoformat(timestamp)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return int(parsed.timestamp() * 1_000_000_000)
            except (ValueError, OverflowError, OSError):
                pass
        return 0

    @classmethod
    def merge(cls, base_events, ours_events, theirs_events):
        base_ids = [cls.event_id(event) for event in base_events]
        base_set = set(base_ids)
        suffix = {}
        for event in list(ours_events) + list(theirs_events):
            event_id = cls.event_id(event)
            if event_id in base_set:
                continue
            normalized = dict(event)
            normalized["event_id"] = event_id
            normalized.setdefault("format", 2)
            normalized.setdefault(
                "ts_utc_ns", cls.event_time(normalized))
            suffix[event_id] = normalized
        merged_suffix = sorted(
            suffix.values(),
            key=lambda event: (
                cls.event_time(event),
                str(event.get("by", "")),
                cls.event_id(event),
            ),
        )
        merged = []
        for event in base_events:
            normalized = dict(event)
            normalized["event_id"] = cls.event_id(normalized)
            normalized.setdefault("format", 2)
            normalized.setdefault(
                "ts_utc_ns", cls.event_time(normalized))
            merged.append(normalized)
        merged.extend(merged_suffix)
        return merged

    @staticmethod
    def write(path, events):
        payload = "".join(
            json.dumps(
                event, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")) + "\n"
            for event in events
        )
        destination = Path(path).resolve()
        if destination.parent.is_symlink():
            raise UnsafePathError("merge output parent is a symlink")
        _atomic_write(
            destination, payload, boundary=destination.parent)

    @classmethod
    def replay(cls, events, graph_out):
        temp = Path(tempfile.mkdtemp(prefix="mind-merge-replay-"))
        graph = temp / GRAPH_FILE
        hippo = Hippocampus(graph)
        original_now = globals()["_now"]
        previous_by = os.environ.get("MIND_BY")
        previous_session = os.environ.get("MIND_SESSION")
        try:
            for event in events:
                timestamp = event.get("ts")
                try:
                    event_now = datetime.fromisoformat(timestamp)
                except (TypeError, ValueError):
                    event_now = original_now()
                globals()["_now"] = lambda value=event_now: value
                if isinstance(event.get("by"), str):
                    os.environ["MIND_BY"] = event["by"]
                if isinstance(event.get("session"), str):
                    os.environ["MIND_SESSION"] = event["session"]
                else:
                    os.environ.pop("MIND_SESSION", None)
                op = event.get("op")
                if op == "remember" and isinstance(
                        event.get("text"), str):
                    metadata = {
                        key: event[key] for key in (
                            "type", "scope", "authority",
                            "source_trust", "sensitivity",
                            "expires_at", "pinned", "entity", "attr")
                        if key in event and event[key] is not None
                    }
                    hippo.remember(
                        event["text"], metadata=metadata)
                elif op == "confirm" and isinstance(
                        event.get("ids"), list):
                    hippo.bump(event["ids"])
                elif op == "link":
                    left = hippo.nodes.get(event.get("id"))
                    right = hippo.nodes.get(event.get("other"))
                    if left and right:
                        hippo.link(
                            left["text"], right["text"],
                            event.get("relation", "related"))
                elif op == "correct" and isinstance(
                        event.get("old_text"), str) and isinstance(
                        event.get("new_text"), str):
                    hippo.correct(
                        event["old_text"], event["new_text"])
                elif op == "forget" and isinstance(
                        event.get("id"), str):
                    hippo.forget(
                        event["id"], event.get("reason", "merged"))
                elif op == "unlink":
                    hippo.unlink(
                        event.get("id"), event.get("other"))
                elif op == "prune" and isinstance(
                        event.get("ids"), list):
                    with hippo._transaction():
                        for node_id in event["ids"]:
                            if node_id in hippo.nodes:
                                hippo._purge_node(node_id)
            payload = _read_bytes_bounded(
                graph, MAX_GRAPH_BYTES, temp)
            destination = Path(graph_out).resolve()
            if destination.parent.is_symlink():
                raise UnsafePathError(
                    "merge graph output parent is a symlink")
            _atomic_write(
                destination, payload, boundary=destination.parent)
        finally:
            globals()["_now"] = original_now
            if previous_by is None:
                os.environ.pop("MIND_BY", None)
            else:
                os.environ["MIND_BY"] = previous_by
            if previous_session is None:
                os.environ.pop("MIND_SESSION", None)
            else:
                os.environ["MIND_SESSION"] = previous_session
            shutil.rmtree(temp, ignore_errors=True)

    @classmethod
    def merge_files(cls, base, ours, theirs, output=None,
                    graph_out=None):
        base_events = cls.read(base)
        ours_events = cls.read(ours)
        theirs_events = cls.read(theirs)
        merged = cls.merge(base_events, ours_events, theirs_events)
        destination = Path(output or ours)
        cls.write(destination, merged)
        if graph_out:
            cls.replay(merged, graph_out)
        return {
            "base": len(base_events),
            "ours": len(ours_events),
            "theirs": len(theirs_events),
            "merged": len(merged),
            "output": str(destination),
            "graph_out": str(graph_out) if graph_out else None,
        }


class Doctor:
    """Deterministic integrity, operability, and personal recall checks."""

    def __init__(self, root, hippo, active):
        self.root = Path(root).resolve()
        self.dir = self.root / MIND_DIR
        self.hippo = hippo
        self.active = active

    @staticmethod
    def _finding(severity, code, message):
        return {
            "severity": severity,
            "code": code,
            "message": message,
        }

    def run(self):
        findings = []
        storage = StorageManager(self.root, self.hippo).report()
        for name in ("graph", "signals", "active_archive"):
            utilization = storage[name]["utilization"]
            estimate = storage[name].get(
                "estimated_days_to_boundary")
            estimate_text = (
                "; about %.1f days at the observed average" % estimate
                if estimate is not None else "")
            if utilization >= 1.0:
                findings.append(self._finding(
                    "error", "storage-%s" % name,
                    "%s is at or above its lifecycle boundary%s"
                    % (name, estimate_text)))
            elif utilization >= 0.8:
                findings.append(self._finding(
                    "warning", "storage-%s" % name,
                    "%s is %.1f%% of its boundary%s" % (
                        name, utilization * 100, estimate_text)))
        for filename in (
                PRUNE_OUTBOX_FILE, LIFECYCLE_OUTBOX_FILE,
                RESTORE_OUTBOX_FILE):
            if (self.dir / filename).exists():
                findings.append(self._finding(
                    "warning", "pending-recovery",
                    "%s awaits recovery" % filename))
        scheduler = _read_scheduler_state(self.dir)
        if (scheduler["lease_token"]
                and scheduler["lease_until_ns"] < time.time_ns()):
            findings.append(self._finding(
                "warning", "expired-dream-lease",
                "automatic-maintenance lease expired before completion"))
        for target in Active.TARGETS:
            path = self.root / target
            if not path.exists() or path.is_symlink():
                continue
            try:
                payload = _read_bytes_bounded(
                    path, MAX_AUX_BYTES, self.root)
            except (OSError, ValueError) as exc:
                findings.append(self._finding(
                    "warning", "agent-file-unreadable",
                    "%s: %s" % (target, _display_text(exc, 160))))
                continue
            text = payload.decode("utf-8", "replace")
            count = text.count(Active.BEGIN)
            if count != 1:
                findings.append(self._finding(
                    "error", "agent-guard-count",
                    "%s contains %d memory guard blocks" % (
                        target, count)))
            if payload.startswith(b"\xef\xbb\xbf"):
                findings.append(self._finding(
                    "info", "agent-bom", "%s starts with a BOM" % target))
            if b"\r\n" in payload:
                findings.append(self._finding(
                    "info", "agent-crlf",
                    "%s uses CRLF and passed tolerant parsing" % target))
        tmp_count = sum(
            1 for path in self.dir.glob("*.tmp")
            if path.is_file() and not path.is_symlink())
        if tmp_count:
            findings.append(self._finding(
                "warning", "temporary-debris",
                "%d temporary files await age-gated cleanup" % tmp_count))
        reranker = self.hippo.reranker
        if reranker.cmd and reranker.configuration_error:
            findings.append(self._finding(
                "error", "embed-backend",
                reranker.configuration_error))
        horizon = _now() + timedelta(hours=26)
        for node_id, node in self.hippo.nodes.items():
            try:
                created = datetime.fromisoformat(node.get("created", ""))
            except (TypeError, ValueError):
                continue
            if created > horizon:
                findings.append(self._finding(
                    "warning", "clock-anomaly",
                    "memory %s is more than 26 hours in the future"
                    % node_id))
        return {
            "ok": not any(
                item["severity"] == "error" for item in findings),
            "findings": findings,
            "storage": storage,
            "scheduler": scheduler,
            "version": __version__,
        }

    def bench(self):
        valid = [
            (node_id, node)
            for node_id, node in self.hippo.nodes.items()
            if self.hippo._valid_at(node)
        ]
        at_1 = 0
        at_5 = 0
        probes = 0
        for node_id, node in valid[:200]:
            keys = [
                key for key in node.get("keys", [])
                if key not in IDENTITY_KEYS
            ]
            query = " ".join(keys[:3]) or node["text"][:120]
            results, _, _ = self.hippo.recall(query, top_k=5)
            ids = [result[0] for result in results]
            probes += 1
            if ids[:1] == [node_id]:
                at_1 += 1
            if node_id in ids:
                at_5 += 1
        result = {
            "ts": _now().isoformat(),
            "version": __version__,
            "probes": probes,
            "recall_at_1": at_1 / probes if probes else 0.0,
            "recall_at_5": at_5 / probes if probes else 0.0,
        }
        _append_regular(
            self.dir / "doctor.jsonl",
            (json.dumps(result, sort_keys=True) + "\n").encode("utf-8"),
            boundary=self.dir, durable=True)
        return result


class Growth:
    def __init__(self, mind_dir, hippo, cortex):
        self.dir = Path(mind_dir)
        self.hippo = hippo
        self.cortex = cortex

    def digest(self, days=7):
        cutoff = _now() - timedelta(days=days)
        counts = Counter()
        first_event = None
        for event in self.hippo.journal_entries():
            timestamp = event.get("ts")
            try:
                when = datetime.fromisoformat(timestamp)
            except (TypeError, ValueError):
                continue
            if first_event is None or when < first_event:
                first_event = when
            if when >= cutoff:
                counts[event.get("op", "unknown")] += 1
        dream_cycles = 0
        promoted = 0
        pruned = 0
        conflicts = 0
        dream_dir = self.dir / DREAMS_DIR
        if dream_dir.is_dir() and not dream_dir.is_symlink():
            for path in sorted(dream_dir.glob("*.md")):
                try:
                    date = datetime.strptime(
                        path.stem, "%Y-%m-%d")
                except ValueError:
                    continue
                if date < cutoff.replace(
                        hour=0, minute=0, second=0, microsecond=0):
                    continue
                try:
                    text = _read_text_retry(
                        path, max_bytes=MAX_AUX_BYTES,
                        boundary=self.dir)
                except (OSError, ValueError, UnicodeError):
                    continue
                dream_cycles += text.count("# Dream journal")
                for match in re.finditer(
                        r"nodes: \d+ \| pruned: (\d+) \| "
                        r"promoted clusters: (\d+) \| "
                        r"conflicts flagged: (\d+)", text):
                    pruned += int(match.group(1))
                    promoted += int(match.group(2))
                    conflicts += int(match.group(3))
        return {
            "days": days,
            "facts_learned": counts["remember"],
            "facts_confirmed": counts["confirm"],
            "facts_corrected": counts["correct"],
            "facts_forgotten": counts["forget"] + counts["prune"],
            "dream_cycles": dream_cycles,
            "promoted_clusters": promoted,
            "conflicts_flagged": conflicts,
            "current_memories": sum(
                self.hippo._valid_at(node)
                for node in self.hippo.nodes.values()),
            "cortex_topics": len(self.cortex.files()),
            "memory_age_days": (
                max(0, (_now() - first_event).days)
                if first_event is not None else 0),
        }


# ────────────────────────────────────────────────────────────────
