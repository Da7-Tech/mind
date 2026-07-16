class PolicyEngine:
    """Deterministic gate for unprompted capture at the tool boundary."""

    SECRET_PATTERNS = (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"\b(?:sk|rk)-[A-Za-z0-9_-]{16,}\b",
        r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"(?i)\b(?:password|passwd|api[_ -]?key|access[_ -]?token|"
        r"secret)\s*[:=]\s*\S+",
    )
    IDENTITY_PATTERNS = (
        r"(?i)\b(?:my name is|i am called|i live in|my phone|my email)\b",
        r"\b(?:اسمي|رقم هاتفي|بريدي|أسكن في|ايميلي|إيميلي)\b",
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        r"\b\+?[0-9][0-9 ()-]{7,}[0-9]\b",
    )
    TRANSIENT_PATTERNS = (
        r"(?i)\b(?:todo|in progress|working on|fixed bug|opened pr|"
        r"pull request|issue #|commit [0-9a-f]{7,40})\b",
        r"\b(?:قيد العمل|مهمة حالية|أصلحت الخطأ|طلب سحب|التزام)\b",
    )
    IMPERATIVE_PATTERNS = (
        r"(?i)^\s*(?:always|never|ignore|run|execute|delete|send|upload|"
        r"must|do not)\b",
        r"^\s*(?:دائما|دائمًا|أبدا|أبدًا|تجاهل|نفذ|احذف|ارسل|أرسل|يجب)\b",
    )
    TRUST_LEVELS = {"user", "repository", "tool", "untrusted"}
    SLOT_PATTERNS = (
        (
            r"(?i)\b(?:database|persistence|storage engine)\b",
            "database", "engine",
        ),
        (
            r"(?i)\b(?:deploy target|deployment target|hosted on|runs on)\b",
            "deployment", "target",
        ),
        (
            r"(?i)\b(?:formatter|formatting tool|code style)\b",
            "tooling", "formatter",
        ),
        (
            r"(?i)\b(?:authentication|auth mechanism|login provider)\b",
            "authentication", "mechanism",
        ),
        (
            r"(?i)\b(?:merge policy|branch policy)\b",
            "repository", "merge-policy",
        ),
    )

    @classmethod
    def classify(cls, text, source_trust="user", explicit=False):
        text = Hippocampus._validated_text(text)
        if source_trust not in cls.TRUST_LEVELS:
            raise ValueError("unknown source trust: %s" % source_trust)
        if any(re.search(pattern, text) for pattern in cls.SECRET_PATTERNS):
            return {
                "decision": "reject",
                "reason": "secret-or-credential pattern",
                "text": text,
            }
        if not explicit and any(
                re.search(pattern, text) for pattern in cls.IDENTITY_PATTERNS):
            return {
                "decision": "reject",
                "reason": "personal-identity pattern",
                "text": text,
            }
        if not explicit and any(
                re.search(pattern, text) for pattern in cls.TRANSIENT_PATTERNS):
            return {
                "decision": "reject",
                "reason": "transient-task-state pattern",
                "text": text,
            }
        if source_trust == "untrusted":
            reason = (
                "untrusted imperative payload"
                if any(re.search(pattern, text)
                       for pattern in cls.IMPERATIVE_PATTERNS)
                else "untrusted source requires review"
            )
            return {
                "decision": "quarantine",
                "reason": reason,
                "text": text,
            }
        return {
            "decision": "accept",
            "reason": "durable project fact",
            "text": text,
        }

    @classmethod
    def infer_metadata(cls, text):
        """Conservatively type common durable facts and contradiction slots."""
        text = Hippocampus._validated_text(text)
        lowered = text.lower()
        memory_type = "semantic"
        if re.search(
                r"\b(?:decision|decided|policy|must remain|invariant)\b",
                lowered):
            memory_type = "decision"
        elif re.search(
                r"\b(?:convention|command|workflow|procedure|formatter)\b",
                lowered):
            memory_type = "procedural"
        metadata = {"type": memory_type}
        for pattern, entity, attr in cls.SLOT_PATTERNS:
            if re.search(pattern, text):
                metadata.update({"entity": entity, "attr": attr})
                break
        return metadata


class PendingQueue:
    def __init__(self, mind_dir):
        self.dir = Path(mind_dir)
        self.path = self.dir / PENDING_FILE
        self.lock = self.dir / PENDING_LOCK_FILE

    def _read(self):
        if not self.path.exists() or self.path.is_symlink():
            return []
        try:
            data = json.loads(_read_text_retry(
                self.path, max_bytes=MAX_PENDING_BYTES,
                boundary=self.dir))
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError,
                RecursionError):
            return []
        if not isinstance(data, list):
            return []
        return [
            item for item in data[:MAX_PENDING_ITEMS]
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("text"), str)
        ]

    def _write(self, items):
        payload = json.dumps(
            items[:MAX_PENDING_ITEMS], ensure_ascii=False, indent=2)
        if len(payload.encode("utf-8")) > MAX_PENDING_BYTES:
            raise FileLimitError(
                "pending queue exceeds %d bytes" % MAX_PENDING_BYTES)
        _atomic_write(self.path, payload, boundary=self.dir)

    def add(self, text, reason, source_trust):
        with _exclusive_file_lock(self.lock, self.dir):
            items = self._read()
            payload = "%s:%s:%s" % (
                text, source_trust, time.time_ns())
            item = {
                "id": hashlib.sha256(
                    payload.encode("utf-8")).hexdigest()[:16],
                "text": text,
                "reason": _display_text(reason, 160),
                "source_trust": source_trust,
                "created": _now().isoformat(),
            }
            items.append(item)
            self._write(items)
            return item

    def list(self):
        with _exclusive_file_lock(self.lock, self.dir):
            return self._read()

    def pop(self, item_id):
        with _exclusive_file_lock(self.lock, self.dir):
            items = self._read()
            found = None
            keep = []
            for item in items:
                if found is None and item.get("id") == item_id:
                    found = item
                else:
                    keep.append(item)
            if found is not None:
                self._write(keep)
            return found
