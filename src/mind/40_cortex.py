# Layer 3: Cortex — consolidated durable knowledge
# ────────────────────────────────────────────────────────────────
class Cortex:
    BEGIN = "<!-- mind:cortex begin -->"
    END = "<!-- mind:cortex end -->"

    def __init__(self, path):
        self.path = Path(path)

    def files(self):
        if self.path.is_symlink() or not self.path.is_dir():
            return []
        out = []
        try:
            with os.scandir(str(self.path)) as entries:
                for entry in entries:
                    if len(out) >= MAX_CORTEX_FILES:
                        break
                    if not entry.name.endswith(".md") or entry.is_symlink():
                        continue
                    try:
                        if entry.is_file(follow_symlinks=False):
                            out.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            return []
        return sorted(out)

    def promote(self, topic, content):
        if not isinstance(topic, str) or not isinstance(content, str):
            raise ValueError("cortex topic and content must be strings")
        topic = _display_text(topic, 100)
        if not topic:
            raise ValueError("cortex topic must not be empty")
        try:
            content_bytes = content.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("cortex content is not valid UTF-8 text")
        if len(content_bytes) > MAX_AUX_BYTES:
            raise FileLimitError("cortex content exceeds %d bytes"
                                 % MAX_AUX_BYTES)
        if self.path.is_symlink():
            raise ValueError("cortex directory is a symlink")
        _secure_mkdirs(self.path, self.path.parent)
        base = re.sub(r'[^\w؀-ۿ]+', '_', topic).strip('_')[:40]
        suffix = _content_md5(topic.encode("utf-8")).hexdigest()[:8]
        fname = "%s-%s.md" % (base or "topic", suffix)
        fpath = self.path / fname
        if fpath.is_symlink():
            raise ValueError("cortex target is a symlink")
        lock_path = self.path.parent / "cortex.lock"
        with _exclusive_file_lock(lock_path, self.path.parent):
            existing_blocks = []
            user_content = ""
            line_ending = "\n"
            if fpath.exists():
                old = _read_text_retry(
                    fpath, max_bytes=MAX_AUX_BYTES,
                    boundary=self.path.parent)
                line_ending = (
                    "\r\n" if old.count("\r\n") >
                    old.count("\n") - old.count("\r\n") else "\n"
                )
                begin = re.search(
                    r"(?m)^" + re.escape(self.BEGIN) + r"[ \t]*\r?$",
                    old)
                end = re.search(
                    r"(?m)^" + re.escape(self.END) + r"[ \t]*\r?$",
                    old)
                if begin and end and end.start() > begin.end():
                    generated = old[begin.end():end.start()]
                    user_content = old[:begin.start()] + old[end.end():]
                    existing_blocks = self._bullet_blocks(generated)
                else:
                    # Legacy files had no ownership boundary. Preserve every
                    # byte as user-visible legacy material; import only their
                    # bullet blocks into the new generated region.
                    user_content = (
                        line_ending * 2
                        + "<!-- legacy cortex content preserved below -->"
                        + line_ending + old
                    )
                    existing_blocks = self._bullet_blocks(old)
            incoming = self._bullet_blocks(content)
            merged_blocks = list(dict.fromkeys(existing_blocks + incoming))
            body = (line_ending.join(merged_blocks) + line_ending
                    if merged_blocks else "")
            generated = (
                self.BEGIN + line_ending
                + "# " + topic + line_ending * 2
                + "> promoted by dream on %s" % _now().date()
                + line_ending * 2 + body
                + self.END
            )
            _atomic_write(
                fpath, generated + user_content,
                boundary=self.path.parent)
        return str(fpath.relative_to(self.path.parent))

    @staticmethod
    def _bullet_blocks(content):
        """Keep Markdown bullet continuations attached to their fact."""
        blocks = []
        current = []
        for line in (content or "").splitlines():
            if line.startswith("- "):
                if current:
                    blocks.append("\n".join(current))
                current = [line]
            elif current and (line.startswith((" ", "\t")) or not line):
                current.append(line)
            elif current:
                blocks.append("\n".join(current))
                current = []
        if current:
            blocks.append("\n".join(current))
        return blocks


# ────────────────────────────────────────────────────────────────
