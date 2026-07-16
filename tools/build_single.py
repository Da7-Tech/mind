#!/usr/bin/env python3
"""Build or verify the deterministic single-file mind distribution."""
import argparse
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "mind"


def assembled_bytes():
    manifest = json.loads(
        (SOURCE / "source.json").read_text("utf-8"))
    names = manifest.get("fragments")
    if not isinstance(names, list) or not names:
        raise ValueError("source manifest has no fragments")
    payload = b""
    for name in names:
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("invalid fragment name")
        path = SOURCE / name
        part = path.read_bytes()
        compile(part, str(path), "exec")
        payload += part
    compile(payload, str(ROOT / manifest["artifact"]), "exec")
    return payload, ROOT / manifest["artifact"]


def atomic_write(path, payload):
    fd, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    payload, default_output = assembled_bytes()
    output = Path(args.output).resolve() if args.output else default_output
    if args.check:
        if not output.is_file() or output.read_bytes() != payload:
            print("single-file artifact is stale")
            return 1
        print("single-file artifact matches modular source")
        return 0
    atomic_write(output, payload)
    print("built %s (%d bytes)" % (output, len(payload)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
