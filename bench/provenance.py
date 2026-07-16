#!/usr/bin/env python3
"""Shared, privacy-safe provenance for public benchmark reports."""
import hashlib
import platform
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_label(path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return path.name


def reproducible_command(argv=None):
    """Return an exact command without machine-specific output paths."""
    values = list(sys.argv if argv is None else argv)
    cleaned = []
    skip_next = False
    for value in values:
        if skip_next:
            skip_next = False
            continue
        if value == "--json-out":
            skip_next = True
            continue
        if value.startswith("--json-out="):
            continue
        cleaned.append(value)
    if cleaned:
        cleaned[0] = _relative_label(cleaned[0])
    return shlex.join([Path(sys.executable).name] + cleaned)


def repo_provenance(source_paths=()):
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip())
    except (OSError, subprocess.SubprocessError):
        commit, dirty = "unknown", None
    sources = {}
    for path in source_paths:
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = ROOT / resolved
        sources[_relative_label(resolved)] = sha256_file(resolved)
    return {
        "commit": commit,
        "dirty": dirty,
        "mind_sha256": sha256_file(ROOT / "mind.py"),
        "sources": sources,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
    }
