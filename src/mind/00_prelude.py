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
  why <id>             provenance: origin, validity, bounded recent history
  entity "term"        every fact about a term, current and superseded
  dream [--dry-run]    run the sleep cycle (light -> deep -> REM)
  export               regenerate agent rule files
  status               memory health report

Design: docs/DESIGN.md  |  License: MIT  |  https://github.com/Da7-Tech/mind
"""
import sys, os, json, re, time, math, hashlib, tempfile, shlex, stat, threading, subprocess, shutil, signal, queue
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter, defaultdict, deque
from contextlib import contextmanager

__version__ = "7.0.0.dev0"

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
JOURNAL_DIR = "journal"
PRUNE_OUTBOX_FILE = "prune-outbox.json"
SCHEDULER_FILE = "scheduler.json"
SCHEDULER_LOCK_FILE = "scheduler.lock"
RUNTIME_FILE = "runtime.py"
PENDING_FILE = "pending.json"
PENDING_LOCK_FILE = "pending.lock"
LIFECYCLE_OUTBOX_FILE = "lifecycle-outbox.json"
RESTORE_OUTBOX_FILE = "restore-outbox.json"
BACKUPS_DIR = "backups"
#   (signals.jsonl is session telemetry and its observed prefix is consumed
#    by dream without deleting concurrent suffixes; the
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
MAX_GRAPH_BYTES = 50_000_000
MAX_AUX_BYTES = 10_000_000
MAX_QUERY_CHARS = 10_000
MAX_NODES = 10_000
MAX_EDGES = 100_000
MAX_HISTORY_PER_NODE = 100
MAX_JOURNAL_SCAN_BYTES = 100_000_000
MAX_JOURNAL_MATCHES = 10_000
MAX_DREAM_COMPARISONS = 200_000
MAX_PRUNES_PER_CYCLE = 256
MAX_PRUNE_OUTBOX_BYTES = 5_000_000
MAX_PRUNE_BATCH_BYTES = 4_000_000
MAX_SIGNALS_BYTES = 5_000_000
MAX_SCHEDULER_BYTES = 64_000
MAX_PENDING_BYTES = 1_000_000
MAX_PENDING_ITEMS = 1_000
MAX_LIFECYCLE_FILE_BYTES = 500_000_000
ARCHIVE_ROTATE_BYTES = 8_000_000
RECEIPT_TAIL_BYTES = 1_000_000
SCHEDULER_LEASE_SECONDS = 300
MAX_CORTEX_FILES = 1_000
MAX_DREAM_FILES = 10_000
LOCK_TIMEOUT_SECONDS = 30.0
MCP_PROTOCOL_VERSION = "2025-11-25"

DIRECTED_RELATIONS = {
    "depends-on": "required-by",
    "owned-by": "owns",
    "caused-by": "caused",
    "part-of": "has-part",
    "uses": "used-by",
    "blocks": "blocked-by",
    "implements": "implemented-by",
    "deployed-to": "hosts",
}
MEMORY_TYPES = {"semantic", "episodic", "procedural", "decision"}
MEMORY_SCOPES = {"project", "user"}
MEMORY_TRUST = {"user", "repository", "tool", "untrusted"}
MEMORY_SENSITIVITY = {"public", "internal", "sensitive", "secret"}


class UnsafePathError(ValueError):
    """A project path is not a private regular file."""


class FileLimitError(ValueError):
    """A project file exceeds its documented resource bound."""


class StaleTargetError(ValueError):
    """A file changed after it was read for a preserving rewrite."""


_NO_EXPECTATION = object()
_EXPECTED_MISSING = object()


def _content_md5(payload):
    """Compatibility content hash; explicitly non-security on FIPS hosts."""
    try:
        return hashlib.md5(payload, usedforsecurity=False)
    except TypeError:
        return hashlib.md5(payload)


def _now():
    return datetime.now()


def _utc_ns():
    return int(_now().astimezone().timestamp() * 1_000_000_000)


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


def _open_regular(path, flags, mode=0o600, boundary=None):
    """Open one private regular file without following or blocking on it."""
    path = Path(os.path.abspath(str(path)))
    boundary = (Path(os.path.abspath(str(boundary)))
                if boundary is not None else None)
    # The Windows CRT otherwise translates CRLF while os.read/os.write operate
    # on the descriptor, breaking exact-byte preservation and file digests.
    flags |= getattr(os, "O_BINARY", 0)
    before = None
    if os.name == "nt":
        if boundary is not None:
            _reject_symlinked_parents(path, boundary)
        if path.is_symlink():
            raise UnsafePathError("refusing symlink file %s" % path)
        try:
            before = os.lstat(str(path))
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise UnsafePathError(
                    "refusing unsafe file %s" % path)
        except FileNotFoundError:
            before = None
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    parent_fd = None
    try:
        if os.name != "nt" and boundary is not None and \
                os.open in getattr(os, "supports_dir_fd", set()):
            try:
                relative = path.relative_to(boundary)
            except ValueError:
                raise UnsafePathError(
                    "file %s escapes boundary %s" % (path, boundary))
            dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | \
                getattr(os, "O_NOFOLLOW", 0)
            parent_fd = os.open(str(boundary), dir_flags)
            for part in relative.parent.parts:
                next_fd = os.open(part, dir_flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            for attempt in range(20):
                try:
                    fd = os.open(
                        path.name, flags, mode, dir_fd=parent_fd)
                    break
                except FileNotFoundError:
                    # Older macOS/Python combinations can report a transient
                    # ENOENT when two threads O_CREAT the same dir-fd-relative
                    # lock file. Retry only create paths; plain reads preserve
                    # their normal missing-file contract.
                    if not (flags & os.O_CREAT) or attempt == 19:
                        raise
                    time.sleep(0.005)
        else:
            if boundary is not None:
                _reject_symlinked_parents(path, boundary)
            if path.is_symlink():
                raise UnsafePathError("refusing symlink file %s" % path)
            fd = os.open(str(path), flags, mode)
    except OSError as e:
        if isinstance(e, (FileNotFoundError, PermissionError)):
            raise
        raise UnsafePathError("refusing unsafe file %s: %s" % (path, e))
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
    try:
        info = os.fstat(fd)
        read_only = (flags & getattr(os, "O_ACCMODE", 3)) == os.O_RDONLY
        valid_links = info.st_nlink in ((0, 1) if read_only else (1,))
        if not stat.S_ISREG(info.st_mode) or not valid_links:
            raise UnsafePathError(
                "refusing %s: regular, single-link file required" % path)
        if os.name == "nt":
            try:
                after = os.lstat(str(path))
            except FileNotFoundError:
                raise StaleTargetError(
                    "file changed during open: %s" % path)
            if (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino):
                raise StaleTargetError(
                    "file changed during open: %s" % path)
            if before is not None and (
                    before.st_dev, before.st_ino) != (
                    after.st_dev, after.st_ino):
                raise StaleTargetError(
                    "file changed during open: %s" % path)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_text_retry(path, max_bytes=MAX_AUX_BYTES, with_identity=False,
                     boundary=None):
    """Read a file that a concurrent writer may be os.replace-ing this
    very instant: Windows raises transient PermissionError to readers
    during the swap (CI finding — third member of the same sharing
    family, after the write and lock paths). POSIX never retries."""
    for attempt in range(200):
        try:
            fd = _open_regular(
                path, os.O_RDONLY,
                boundary=boundary or Path(path).parent)
            try:
                before = os.fstat(fd)
                size = before.st_size
                if size > max_bytes:
                    raise FileLimitError(
                        "%s exceeds the %d-byte limit" % (path, max_bytes))
                chunks = []
                remaining = max_bytes + 1
                while remaining:
                    chunk = os.read(fd, min(1_048_576, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                payload = b"".join(chunks)
                if len(payload) > max_bytes:
                    raise FileLimitError(
                        "%s exceeds the %d-byte limit" % (path, max_bytes))
                after = os.fstat(fd)
                if (before.st_dev, before.st_ino, before.st_mtime_ns,
                        before.st_size) != (
                        after.st_dev, after.st_ino, after.st_mtime_ns,
                        after.st_size):
                    if attempt == 199:
                        raise StaleTargetError(
                            "%s changed while it was being read" % path)
                    continue
                text = payload.decode("utf-8")
                if with_identity:
                    return text, (
                        after.st_dev, after.st_ino,
                        after.st_mtime_ns, after.st_size)
                return text
            finally:
                os.close(fd)
        except PermissionError:
            if os.name != "nt" or attempt == 199:
                raise
            time.sleep(0.05)
        except StaleTargetError:
            if attempt == 199:
                raise
            time.sleep(0.005)


def _append_regular(path, payload, boundary, mode=0o600, durable=False):
    """Append one record to a private regular file, never a FIFO or hard link."""
    fd = _open_regular(
        path, os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        mode=mode, boundary=boundary)
    try:
        _write_once(fd, payload)
        if durable:
            os.fsync(fd)
    finally:
        os.close(fd)


def _secure_mkdirs(path, boundary, mode=0o700):
    """Create a directory chain without following a swapped parent."""
    path = Path(os.path.abspath(str(path)))
    boundary = Path(os.path.abspath(str(boundary)))
    try:
        relative = path.relative_to(boundary)
    except ValueError:
        raise UnsafePathError(
            "directory %s escapes boundary %s" % (path, boundary))
    if os.name != "nt" and os.mkdir in getattr(
            os, "supports_dir_fd", set()):
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | \
            getattr(os, "O_NOFOLLOW", 0)
        try:
            current = os.open(str(boundary), flags)
        except OSError as e:
            raise UnsafePathError(
                "refusing unsafe boundary %s: %s" % (boundary, e))
        try:
            for part in relative.parts:
                try:
                    next_fd = os.open(part, flags, dir_fd=current)
                except FileNotFoundError:
                    try:
                        os.mkdir(part, mode, dir_fd=current)
                    except FileExistsError:
                        # Another process/thread created the same directory
                        # after our failed open. Re-open and validate it
                        # through the held parent descriptor.
                        pass
                    next_fd = os.open(part, flags, dir_fd=current)
                except OSError as e:
                    raise UnsafePathError(
                        "refusing unsafe directory %s: %s" % (path, e))
                os.close(current)
                current = next_fd
        finally:
            os.close(current)
        return
    _reject_symlinked_parents(path / "_", boundary)
    if path.is_symlink():
        raise UnsafePathError("refusing symlink directory %s" % path)
    path.mkdir(parents=True, exist_ok=True, mode=mode)


@contextmanager
def _exclusive_file_lock(path, boundary, timeout=LOCK_TIMEOUT_SECONDS):
    """Portable advisory lock for non-graph read/modify/write artifacts."""
    fd = _open_regular(
        path, os.O_RDWR | os.O_CREAT, boundary=boundary)
    lockf = os.fdopen(fd, "r+b", buffering=0)
    backend = None
    try:
        if os.fstat(lockf.fileno()).st_size == 0:
            lockf.write(b"\0")
            lockf.flush()
            os.fsync(lockf.fileno())
        try:
            import fcntl
        except ImportError:
            try:
                import msvcrt
            except ImportError:
                raise RuntimeError("no supported file-lock backend")
            deadline = time.monotonic() + timeout
            mode = getattr(msvcrt, "LK_NBLCK", msvcrt.LK_LOCK)
            while True:
                lockf.seek(0)
                try:
                    msvcrt.locking(lockf.fileno(), mode, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError(
                            "could not acquire %s within %.1f seconds"
                            % (path, timeout))
                    time.sleep(0.05)
            backend = ("msvcrt", msvcrt)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(
                        lockf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError(
                            "could not acquire %s within %.1f seconds"
                            % (path, timeout))
                    time.sleep(0.05)
            backend = ("fcntl", fcntl)
        yield
    finally:
        if backend is not None:
            name, module = backend
            if name == "fcntl":
                module.flock(lockf.fileno(), module.LOCK_UN)
            else:
                lockf.seek(0)
                module.locking(lockf.fileno(), module.LK_UNLCK, 1)
        lockf.close()


def _display_text(value, cap=1000):
    """One-line, terminal-safe rendering of project-controlled data."""
    if not isinstance(value, str):
        value = str(value)
    value = re.sub(
        u"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069\ud800-\udfff]",
        "", value)
    return " ".join(value.split())[:cap]


class JournalEntries(list):
    def __init__(self, values=(), total_count=None):
        super().__init__(values)
        self.total_count = len(self) if total_count is None else total_count


def _latest_dream_stem(path):
    if path.is_symlink() or not path.is_dir():
        return None
    latest = None
    seen = 0
    try:
        with os.scandir(str(path)) as entries:
            for entry in entries:
                if seen >= MAX_DREAM_FILES:
                    break
                seen += 1
                if entry.is_symlink() or not re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}\.md", entry.name):
                    continue
                if latest is None or entry.name > latest:
                    latest = entry.name
    except OSError:
        return None
    return latest[:-3] if latest else None


def _sweep_tmp_files(mind_dir, min_age_seconds=24 * 3600):
    """Remove only old regular temporary files directly under `.mind/`."""
    mind_dir = Path(mind_dir)
    now = _now().timestamp()
    removed = 0
    if mind_dir.is_symlink() or not mind_dir.is_dir():
        return removed
    try:
        entries = list(os.scandir(str(mind_dir)))[:10_000]
    except OSError:
        return removed
    for entry in entries:
        if entry.is_symlink() or not entry.name.endswith(".tmp"):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
            valid_links = info.st_nlink in (
                (0, 1) if os.name == "nt" else (1,))
            if (not stat.S_ISREG(info.st_mode) or not valid_links
                    or now - info.st_mtime < min_age_seconds):
                continue
            Path(entry.path).unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _read_tail_text(path, max_bytes, boundary):
    """Read a bounded UTF-8 tail without loading an append-only artifact."""
    fd = _open_regular(path, os.O_RDONLY, boundary=boundary)
    try:
        size = os.fstat(fd).st_size
        if size > max_bytes:
            os.lseek(fd, size - max_bytes, os.SEEK_SET)
        chunks = []
        remaining = min(size, max_bytes)
        while remaining:
            chunk = os.read(fd, min(1_048_576, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks).decode("utf-8", "replace")
    finally:
        os.close(fd)


def _read_bytes_bounded(path, max_bytes, boundary):
    fd = _open_regular(path, os.O_RDONLY, boundary=boundary)
    try:
        size = os.fstat(fd).st_size
        if size > max_bytes:
            raise FileLimitError(
                "%s exceeds the %d-byte lifecycle limit"
                % (path, max_bytes))
        chunks = []
        remaining = size
        while remaining:
            chunk = os.read(fd, min(1_048_576, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _scheduler_default():
    return {
        "format": 1,
        "pending": 0,
        "last_dream_ns": 0,
        "lease_token": None,
        "lease_until_ns": 0,
        "lease_pending": 0,
    }


def _read_scheduler_state(mind_dir):
    path = Path(mind_dir) / SCHEDULER_FILE
    state = _scheduler_default()
    if not path.exists() or path.is_symlink():
        return state
    try:
        raw = json.loads(_read_text_retry(
            path, max_bytes=MAX_SCHEDULER_BYTES, boundary=mind_dir))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError,
            RecursionError):
        return state
    if not isinstance(raw, dict):
        return state
    for field in ("pending", "last_dream_ns", "lease_until_ns",
                  "lease_pending"):
        value = raw.get(field, state[field])
        if isinstance(value, bool) or not isinstance(value, int):
            value = state[field]
        state[field] = max(0, value)
    token = raw.get("lease_token")
    state["lease_token"] = token[:128] if isinstance(token, str) else None
    return state


def _update_scheduler_state(mind_dir, update, use_lock=True):
    mind_dir = Path(mind_dir)
    def apply_update():
        state = _read_scheduler_state(mind_dir)
        result = update(state)
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True)
        if len(payload.encode("utf-8")) > MAX_SCHEDULER_BYTES:
            raise FileLimitError("scheduler state exceeds %d bytes"
                                 % MAX_SCHEDULER_BYTES)
        _atomic_write(
            mind_dir / SCHEDULER_FILE, payload, boundary=mind_dir)
        return result
    if not use_lock:
        return apply_update()
    lock = mind_dir / SCHEDULER_LOCK_FILE
    with _exclusive_file_lock(lock, mind_dir):
        return apply_update()


def _scheduler_note_signals(mind_dir, count=1):
    def update(state):
        state["pending"] = min(
            1_000_000_000, state["pending"] + max(0, int(count)))
    _update_scheduler_state(mind_dir, update)


def _scheduler_note_signal(mind_dir):
    _scheduler_note_signals(mind_dir, 1)


def _scheduler_claim(mind_dir):
    now_ns = time.time_ns()
    stale_ns = int(AUTO_DREAM_HOURS * 3600 * 1_000_000_000)

    def update(state):
        if state["lease_token"] and state["lease_until_ns"] > now_ns:
            return None
        stale = (
            state["last_dream_ns"] == 0
            or now_ns - state["last_dream_ns"] >= stale_ns
        )
        if state["pending"] == 0 or not (
                state["pending"] >= AUTO_DREAM_SIGNALS or stale):
            state["lease_token"] = None
            state["lease_until_ns"] = 0
            state["lease_pending"] = 0
            return None
        token = hashlib.sha256((
            "%d:%d:%d:%d" % (
                os.getpid(), threading.get_ident(), now_ns,
                state["pending"])
        ).encode("ascii")).hexdigest()[:32]
        state["lease_token"] = token
        state["lease_until_ns"] = (
            now_ns + SCHEDULER_LEASE_SECONDS * 1_000_000_000)
        state["lease_pending"] = state["pending"]
        return token

    return _update_scheduler_state(mind_dir, update)


def _scheduler_complete(mind_dir, token):
    now_ns = time.time_ns()

    def update(state):
        if state["lease_token"] != token:
            return False
        state["pending"] = max(
            0, state["pending"] - state["lease_pending"])
        state["last_dream_ns"] = now_ns
        state["lease_token"] = None
        state["lease_until_ns"] = 0
        state["lease_pending"] = 0
        return True

    return _update_scheduler_state(mind_dir, update)


def _scheduler_release(mind_dir, token):
    def update(state):
        if state["lease_token"] != token:
            return False
        state["lease_token"] = None
        state["lease_until_ns"] = 0
        state["lease_pending"] = 0
        return True

    return _update_scheduler_state(mind_dir, update)


def _write_all(fd, data):
    """Write every byte or raise. os.write() may legally return short."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write: os.write made no progress")
        view = view[written:]


def _write_once(fd, data):
    """One append syscall; reject a short record instead of hiding it."""
    written = os.write(fd, data)
    if written != len(data):
        # Isolate the damaged fragment so it cannot swallow the next JSONL
        # record. The current record is reported lost; later provenance stays
        # parseable even under an injected short write or a full filesystem.
        try:
            os.write(fd, b"\n")
        except OSError:
            pass
        raise OSError("partial append: wrote %d of %d bytes" % (
            written, len(data)))


def _atomic_write(path, data, boundary=None,
                  expected_identity=_NO_EXPECTATION, mode=0o600):
    """Atomic, symlink-safe, durable write: O_NOFOLLOW + fsync + os.replace.

    O_NOFOLLOW + the is_symlink check block TOCTOU symlink attacks on the
    target itself; when `boundary` is given, parent directories up to it are
    also checked so a symlinked parent dir cannot redirect the write outside
    the trust boundary. os.replace guarantees readers see the old or the new
    file, never a torn one; fsync before the rename makes the new content
    survive power loss (without it the rename can land while the data is
    still in page cache)."""
    path = Path(os.path.abspath(str(path)))
    boundary = Path(os.path.abspath(str(boundary or path.parent)))
    payload = data.encode("utf-8") if isinstance(data, str) else data

    # POSIX dir-fd traversal makes the checked directory chain the exact
    # chain used by create and replace. Renaming a parent between a
    # path-based check and os.replace can no longer redirect the write.
    if os.name != "nt" and os.rename in getattr(
            os, "supports_dir_fd", set()):
        try:
            relative = path.relative_to(boundary)
        except ValueError:
            raise ValueError("path %s escapes the trust boundary %s"
                             % (path, boundary))
        parts = relative.parent.parts
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | \
            getattr(os, "O_NOFOLLOW", 0)
        try:
            parent_fd = os.open(str(boundary), dir_flags)
        except OSError as e:
            raise UnsafePathError(
                "refusing unsafe boundary %s: %s" % (boundary, e))
        try:
            for part in parts:
                try:
                    next_fd = os.open(part, dir_flags, dir_fd=parent_fd)
                except OSError as e:
                    raise UnsafePathError(
                        "refusing unsafe parent for %s: %s" % (path, e))
                os.close(parent_fd)
                parent_fd = next_fd
            target_mode = mode
            try:
                current = os.stat(
                    path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                current = None
            identity = (
                current.st_dev, current.st_ino,
                current.st_mtime_ns, current.st_size
            ) if current is not None else _EXPECTED_MISSING
            if expected_identity is not _NO_EXPECTATION and \
                    identity != expected_identity:
                raise StaleTargetError(
                    "%s changed after it was read" % path)
            if current is not None:
                if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                    raise UnsafePathError(
                        "refusing unsafe atomic-write target: %s" % path)
                target_mode = stat.S_IMODE(current.st_mode)
            tmp_name = ".%s.%d.%d.%d.tmp" % (
                path.name, os.getpid(), threading.get_ident(), time.time_ns())
            fd = os.open(
                tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                getattr(os, "O_NOFOLLOW", 0), target_mode, dir_fd=parent_fd)
            replaced = False
            try:
                try:
                    try:
                        os.fchmod(fd, target_mode)
                    except (AttributeError, OSError):
                        pass
                    _write_all(fd, payload)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                    fd = None
                if expected_identity is not _NO_EXPECTATION:
                    try:
                        latest = os.stat(
                            path.name, dir_fd=parent_fd,
                            follow_symlinks=False)
                        latest_identity = (
                            latest.st_dev, latest.st_ino,
                            latest.st_mtime_ns, latest.st_size)
                    except FileNotFoundError:
                        latest_identity = _EXPECTED_MISSING
                    if latest_identity != expected_identity:
                        raise StaleTargetError(
                            "%s changed during rewrite" % path)
                os.replace(tmp_name, path.name, src_dir_fd=parent_fd,
                           dst_dir_fd=parent_fd)
                replaced = True
                os.fsync(parent_fd)
            finally:
                if fd is not None:
                    os.close(fd)
                if not replaced:
                    try:
                        os.unlink(tmp_name, dir_fd=parent_fd)
                    except OSError:
                        pass
        finally:
            os.close(parent_fd)
        return

    # Windows fallback: no dir-fd APIs, but target and every parent are
    # checked immediately before an unpredictable O_EXCL temporary is
    # replaced. Existing private permissions are preserved.
    if path.is_symlink():
        raise ValueError("refusing to write through a symlink: %s" % path)
    _reject_symlinked_parents(path, boundary)
    target_mode = mode
    if path.exists():
        current = os.lstat(str(path))
        if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
            raise UnsafePathError(
                "refusing unsafe atomic-write target: %s" % path)
        target_mode = stat.S_IMODE(current.st_mode)
        identity = (current.st_dev, current.st_ino,
                    current.st_mtime_ns, current.st_size)
    else:
        identity = _EXPECTED_MISSING
    if expected_identity is not _NO_EXPECTATION and \
            identity != expected_identity:
        raise StaleTargetError("%s changed after it was read" % path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                               dir=str(path.parent))
    replaced = False
    try:
        try:
            try:
                os.fchmod(fd, target_mode)
            except (AttributeError, OSError):
                pass
            _write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
            fd = None
        if expected_identity is not _NO_EXPECTATION:
            if path.exists():
                latest = os.lstat(str(path))
                latest_identity = (
                    latest.st_dev, latest.st_ino,
                    latest.st_mtime_ns, latest.st_size)
            else:
                latest_identity = _EXPECTED_MISSING
            if latest_identity != expected_identity:
                raise StaleTargetError(
                    "%s changed during rewrite" % path)
        for attempt in range(200):
            try:
                os.replace(tmp, str(path))
                replaced = True
                break
            except PermissionError:
                if os.name != "nt" or attempt == 199:
                    raise
                time.sleep(0.05)
    finally:
        if fd is not None:
            os.close(fd)
        if not replaced:
            try:
                os.unlink(tmp)
            except OSError:
                pass


# ────────────────────────────────────────────────────────────────
