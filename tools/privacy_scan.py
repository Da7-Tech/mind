#!/usr/bin/env python3
"""Fail on personal paths, private audit material, or likely real secrets."""
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {
    ".json", ".jsonl", ".md", ".py", ".toml", ".txt", ".yml", ".yaml",
}
PATTERN_DEFINITION_FILES = {
    "tools/claims.py",
    "tools/privacy_scan.py",
}
PATTERNS = (
    ("personal home path", re.compile(
        r"(?:/Users/|/home/)[^/\s]+/|(?i:[A-Z]:\\\\Users\\\\[^\\\\\s]+\\\\)")),
    ("private temporary path", re.compile(
        r"/(?:private/)?var/folders/|/tmp/")),
    ("owner personal name", re.compile(r"(?i)\braif\b|رائف")),
    ("private audit artifact", re.compile(
        r"mind-final-consolidated-audit|mind-implementation-task")),
    ("email address", re.compile(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("likely live OpenAI token", re.compile(
        r"\bsk-(?!example|test|dummy)[A-Za-z0-9_-]{20,}\b")),
    ("private key", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)


def git_paths(include_untracked):
    args = ["git", "ls-files"]
    if include_untracked:
        args.extend(["--cached", "--others", "--exclude-standard"])
    output = subprocess.check_output(
        args, cwd=ROOT, text=True, stderr=subprocess.DEVNULL)
    return sorted(set(output.splitlines()))


def scan(paths):
    findings = []
    for relative in paths:
        if relative in PATTERN_DEFINITION_FILES:
            continue
        path = ROOT / relative
        if not path.is_file() or (
                path.suffix.lower() not in TEXT_SUFFIXES
                and path.name not in ("LICENSE", "CODEOWNERS")):
            continue
        try:
            text = path.read_text("utf-8")
        except UnicodeError:
            continue
        for label, pattern in PATTERNS:
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                findings.append((relative, line, label))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tracked", action="store_true",
        help="Scan tracked files only; default also scans untracked files.")
    args = parser.parse_args(argv)
    findings = scan(git_paths(include_untracked=not args.tracked))
    if findings:
        for path, line, label in findings:
            print("%s:%d: %s" % (path, line, label))
        return 1
    print("privacy scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
