class CommandEmbed:
    """Optional command-backed embeddings for recall re-ranking.

    The command is read from MIND_EMBED_CMD by default. It receives the text
    on stdin and should print either a JSON list of numbers or whitespace/
    comma-separated floats. Any failure falls back to HashEmbed, so default
    offline behaviour and zero-dependency installs stay unchanged.
    """

    MAX_OUTPUT_BYTES = 1_000_000
    MAX_VECTOR_DIM = 8192
    MAX_CACHE_BYTES = 16_000_000
    FAILURE_CACHE_SECONDS = 5.0

    def __init__(self, cmd=None, fallback=None, timeout=None,
                 budget=None, project_root=None):
        raw_cmd = cmd if cmd is not None else os.environ.get("MIND_EMBED_CMD", "")
        self.cmd = str(raw_cmd or "").strip()
        self.fallback = fallback if fallback is not None else HashEmbed()
        self.timeout = self._timeout(timeout)
        self.budget = self._budget(budget)
        self.project_root = Path(project_root or os.getcwd()).resolve()
        self.argv, self.configuration_error = self._resolve_command(self.cmd)
        self.server_cmd = str(
            os.environ.get("MIND_EMBED_SERVER", "") or "").strip()
        self.server_argv, self.server_configuration_error = \
            self._resolve_command(self.server_cmd)
        self._server_process = None
        self._server_info = None
        self._cache = {}
        self._cache_bytes = 0
        self.last_report = {
            "backend": "offline",
            "model": None,
            "calls": 0,
            "latency_ms": 0.0,
            "fallback": False,
            "reason": None,
        }

    @staticmethod
    def _timeout(value):
        if value is None:
            value = os.environ.get("MIND_EMBED_TIMEOUT", "2.0")
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 2.0
        return max(0.1, min(30.0, value))

    @staticmethod
    def _budget(value):
        if value is None:
            value = os.environ.get("MIND_EMBED_BUDGET", "3.0")
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 3.0
        return max(0.1, min(30.0, value))

    @staticmethod
    def _parse_vector(payload):
        text = (payload or b"").decode("utf-8", "replace").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                for key in ("embedding", "vector", "values"):
                    if key in data:
                        data = data[key]
                        break
            if isinstance(data, list):
                vec = [float(v) for v in data]
                return CommandEmbed._valid_vector(vec)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        parts = [p for p in re.split(r"[\s,]+", text) if p]
        try:
            vec = [float(p) for p in parts]
        except ValueError:
            return None
        return CommandEmbed._valid_vector(vec)

    @staticmethod
    def _valid_vector(vec):
        return (
            vec if vec and len(vec) <= CommandEmbed.MAX_VECTOR_DIM
            and any(vec) and all(math.isfinite(v) for v in vec) else None
        )

    @staticmethod
    def _split_command(cmd, platform=None):
        try:
            parts = shlex.split(cmd, posix=(platform or os.name) != "nt")
        except ValueError:
            return None
        if (platform or os.name) == "nt":
            parts = [
                p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in ("'", '"') else p
                for p in parts
            ]
        return parts

    def _resolve_command(self, cmd):
        if not cmd:
            return None, None
        argv = self._split_command(cmd)
        if not argv:
            return None, "invalid command syntax"
        program = argv[0]
        has_separator = os.sep in program or (
            os.altsep is not None and os.altsep in program)
        if os.path.isabs(program):
            resolved = program
        elif has_separator:
            resolved = str((self.project_root / program).resolve())
        else:
            resolved = shutil.which(program)
        if not resolved or not Path(resolved).is_file():
            return None, "program not found: %s" % program
        argv[0] = resolved
        return argv, None

    @staticmethod
    def _terminate_process_group(proc):
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            pass

    @staticmethod
    def _frame(payload):
        return (
            str(len(payload)).encode("ascii") + b"\n" + payload
        )

    @staticmethod
    def _read_frame(stream):
        header = stream.readline()
        if not header:
            raise EOFError("embedding server closed stdout")
        try:
            size = int(header.strip())
        except ValueError:
            raise ValueError("invalid embedding server frame length")
        if size < 0 or size > CommandEmbed.MAX_OUTPUT_BYTES:
            raise ValueError("embedding server frame exceeds limit")
        payload = stream.read(size)
        if len(payload) != size:
            raise EOFError("truncated embedding server frame")
        return payload

    def _close_server(self):
        proc = self._server_process
        self._server_process = None
        self._server_info = None
        if proc is None:
            return
        self._terminate_process_group(proc)
        try:
            proc.wait(timeout=1)
        except (OSError, subprocess.SubprocessError):
            pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass

    def close(self):
        self._close_server()

    def __del__(self):
        try:
            self._close_server()
        except Exception:
            pass

    def _server_exchange(self, request, timeout):
        if not self.server_argv:
            return None, (
                self.server_configuration_error or
                "embedding server disabled"), 0.0
        started = time.perf_counter()
        if self._server_process is None:
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt" else 0
            )
            try:
                self._server_process = subprocess.Popen(
                    self.server_argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    start_new_session=(os.name != "nt"),
                    creationflags=creationflags,
                )
            except OSError as exc:
                return None, "server start failed: %s" % _display_text(
                    exc, 120), 0.0
        proc = self._server_process
        try:
            payload = json.dumps(
                request, ensure_ascii=False,
                separators=(",", ":")).encode("utf-8")
            proc.stdin.write(self._frame(payload))
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            self._close_server()
            return None, "server write failed: %s" % _display_text(
                exc, 120), (
                    time.perf_counter() - started) * 1000
        result_queue = queue.Queue(maxsize=1)

        def read_response():
            try:
                result_queue.put((self._read_frame(proc.stdout), None))
            except Exception as exc:
                result_queue.put((None, exc))

        reader = threading.Thread(target=read_response, daemon=True)
        reader.start()
        try:
            response, error = result_queue.get(timeout=timeout)
        except queue.Empty:
            self._close_server()
            return None, "server deadline exceeded", (
                time.perf_counter() - started) * 1000
        if error is not None:
            self._close_server()
            return None, "server read failed: %s" % _display_text(
                error, 120), (
                    time.perf_counter() - started) * 1000
        try:
            decoded = json.loads(response.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            self._close_server()
            return None, "invalid server JSON", (
                time.perf_counter() - started) * 1000
        return decoded, None, (
            time.perf_counter() - started) * 1000

    def _ensure_server_handshake(self):
        if self._server_info is not None:
            return self._server_info, None, 0.0
        response, error, latency = self._server_exchange({
            "protocol": "mind-embed-server-v1",
            "op": "handshake",
        }, self.budget)
        if error is not None:
            return None, error, latency
        if not isinstance(response, dict) or response.get(
                "protocol") != "mind-embed-server-v1":
            self._close_server()
            return None, "invalid server handshake", latency
        dimension = response.get("dimension")
        if not isinstance(dimension, int) or not (
                1 <= dimension <= self.MAX_VECTOR_DIM):
            self._close_server()
            return None, "invalid server dimension", latency
        self._server_info = {
            "model_id": _display_text(
                response.get("model_id", "unknown"), 120),
            "revision": _display_text(
                response.get("revision", "unknown"), 120),
            "dimension": dimension,
        }
        return self._server_info, None, latency

    def _server_embeddings(self, texts):
        info, error, handshake_latency = self._ensure_server_handshake()
        if error is not None:
            return None, None, error, handshake_latency
        response, error, latency = self._server_exchange({
            "protocol": "mind-embed-server-v1",
            "op": "embed",
            "texts": texts,
        }, self.budget)
        if error is not None:
            return None, info, error, handshake_latency + latency
        vectors = (
            response.get("vectors") if isinstance(response, dict)
            else None)
        encoded = json.dumps({"vectors": vectors}).encode("utf-8")
        parsed, _, parse_error = self._parse_batch(
            encoded, len(texts))
        if parsed is not None and any(
                len(vector) != info["dimension"] for vector in parsed):
            parsed, parse_error = None, "server dimension changed"
        return (
            parsed, info, parse_error,
            handshake_latency + latency)

    def _run_payload(self, payload, timeout):
        if not self.argv:
            return None, self.configuration_error or "backend disabled", 0.0
        started = time.perf_counter()
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if os.name == "nt" else 0
        )
        try:
            with tempfile.TemporaryFile() as stdout:
                proc = subprocess.Popen(
                    self.argv,
                    stdin=subprocess.PIPE,
                    stdout=stdout,
                    stderr=subprocess.DEVNULL,
                    start_new_session=(os.name != "nt"),
                    creationflags=creationflags,
                )
                try:
                    proc.communicate(input=payload, timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._terminate_process_group(proc)
                    proc.wait()
                    return None, "total deadline exceeded", (
                        time.perf_counter() - started) * 1000
                size = stdout.tell()
                if proc.returncode != 0:
                    return None, "backend exited %d" % proc.returncode, (
                        time.perf_counter() - started) * 1000
                if size > self.MAX_OUTPUT_BYTES:
                    return None, "backend output exceeded limit", (
                        time.perf_counter() - started) * 1000
                stdout.seek(0)
                result = stdout.read(self.MAX_OUTPUT_BYTES + 1)
                return result, None, (
                    time.perf_counter() - started) * 1000
        except (OSError, subprocess.SubprocessError) as exc:
            return None, "backend error: %s" % _display_text(exc, 120), (
                time.perf_counter() - started) * 1000

    def _command_embed(self, text):
        if not self.argv:
            return None
        payload, error, _ = self._run_payload(
            (text or "").encode("utf-8"), self.timeout)
        if error is not None:
            return None
        return self._parse_vector(payload)

    def _parse_batch(self, payload, expected):
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            return None, None, "invalid batch response"
        model = None
        if isinstance(data, dict):
            if data.get("protocol") not in (None, "mind-embed-v1"):
                return None, None, "unsupported batch protocol"
            model = data.get("model")
            vectors = data.get("vectors")
        else:
            vectors = data
        if not isinstance(vectors, list) or len(vectors) != expected:
            return None, None, "partial batch response"
        clean = []
        dimension = None
        for raw in vectors:
            if not isinstance(raw, list):
                return None, None, "invalid vector in batch"
            try:
                vec = [float(value) for value in raw]
            except (TypeError, ValueError, OverflowError):
                return None, None, "invalid vector in batch"
            vec = self._valid_vector(vec)
            if vec is None:
                return None, None, "invalid vector in batch"
            if dimension is None:
                dimension = len(vec)
            if len(vec) != dimension:
                return None, None, "inconsistent vector dimensions"
            clean.append(vec)
        return clean, (
            _display_text(model, 120) if isinstance(model, str) else None
        ), None

    @staticmethod
    def _cosine(va, vb):
        dot = sum(x * y for x, y in zip(va, vb))
        na = math.sqrt(sum(x * x for x in va)) or 1.0
        nb = math.sqrt(sum(y * y for y in vb)) or 1.0
        return max(0.0, dot / (na * nb))

    def similarities(self, query, candidates):
        """Return one metric for the whole ranking, never per-item fallback."""
        candidates = list(candidates)
        if not candidates:
            return []
        texts = [query] + candidates
        if self.server_argv:
            vectors, info, error, latency = self._server_embeddings(texts)
            if vectors is not None:
                query_vector = vectors[0]
                self.last_report = {
                    "backend": "server",
                    "model": "%s@%s" % (
                        info["model_id"], info["revision"]),
                    "calls": 1,
                    "latency_ms": latency,
                    "fallback": False,
                    "reason": None,
                }
                return [
                    self._cosine(query_vector, candidate)
                    for candidate in vectors[1:]
                ]
            self.last_report = {
                "backend": "offline",
                "model": None,
                "calls": 1,
                "latency_ms": latency,
                "fallback": True,
                "reason": error or "embedding server failed",
            }
            return [
                self.fallback.similarity(query, text)
                for text in candidates]
        if not self.argv:
            reason = self.configuration_error
            self.last_report = {
                "backend": "offline", "model": None, "calls": 0,
                "latency_ms": 0.0, "fallback": bool(self.cmd),
                "reason": reason,
            }
            return [self.fallback.similarity(query, text)
                    for text in candidates]
        request = json.dumps(
            {"protocol": "mind-embed-v1", "texts": texts},
            ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        payload, error, latency = self._run_payload(request, self.budget)
        vectors, model, parse_error = (
            self._parse_batch(payload, len(texts))
            if payload is not None else (None, None, None)
        )
        reason = error or parse_error
        if vectors is None:
            self.last_report = {
                "backend": "offline", "model": None, "calls": 1,
                "latency_ms": latency, "fallback": True,
                "reason": reason or "batch backend failed",
            }
            return [self.fallback.similarity(query, text)
                    for text in candidates]
        query_vector = vectors[0]
        self.last_report = {
            "backend": "command", "model": model, "calls": 1,
            "latency_ms": latency, "fallback": False, "reason": None,
        }
        return [
            self._cosine(query_vector, candidate)
            for candidate in vectors[1:]
        ]

    def _embed_with_source(self, text):
        text = text or ""
        if not self.cmd:
            return "fallback", self.fallback.embed(text)

        now = time.monotonic()
        cached = self._cache.get(text)
        if cached is not None:
            source, vec, retry_at = cached
            if source == "command" or now < retry_at:
                return source, vec

        vec = self._command_embed(text)
        if vec is None:
            source = "fallback"
            vec = self.fallback.embed(text)
            retry_at = now + self.FAILURE_CACHE_SECONDS
        else:
            source = "command"
            retry_at = float("inf")
        vector_bytes = len(vec) * 32
        if text in self._cache:
            self._cache[text] = (source, vec, retry_at)
        elif (len(self._cache) < 4096 and
              self._cache_bytes + vector_bytes <= self.MAX_CACHE_BYTES):
            self._cache[text] = (source, vec, retry_at)
            self._cache_bytes += vector_bytes
        return source, vec

    def embed(self, text):
        return self._embed_with_source(text)[1]

    def similarity(self, a, b):
        source_a, va = self._embed_with_source(a)
        source_b, vb = self._embed_with_source(b)
        if (
            source_a != "command"
            or source_b != "command"
            or not va
            or not vb
            or len(va) != len(vb)
        ):
            return self.fallback.similarity(a, b)
        return self._cosine(va, vb)


# ────────────────────────────────────────────────────────────────
