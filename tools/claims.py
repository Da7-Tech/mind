#!/usr/bin/env python3
"""Generate and verify mind's machine-readable claims and documentation."""
import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT_PATH = ROOT / "docs" / "project.json"
FACTS_PATH = ROOT / "docs" / "facts.json"
DOCS = (ROOT / "README.md", ROOT / "README.ar.md", ROOT / "SKILL.md")
BEGIN = "<!-- mind:facts begin -->"
END = "<!-- mind:facts end -->"
BENCH_BEGIN = "<!-- mind:benchmarks begin -->"
BENCH_END = "<!-- mind:benchmarks end -->"
PERSONAL_PATTERNS = (
    re.compile(r"/Users/[^/\s]+/"),
    re.compile(r"/home/[^/\s]+/"),
    re.compile(r"(?i)[A-Z]:\\\\Users\\\\[^\\\\\s]+\\\\"),
    re.compile(r"/(?:private/)?var/folders/"),
    re.compile(r"/tmp/"),
    re.compile(
        r"/(?:opt/homebrew|usr/local)/(?:Cellar|opt)/"
        r"|/Library/Frameworks/Python\.framework/Versions/"),
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command_names(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
                isinstance(target, ast.Name)
                and target.id == "COMMANDS"
                for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        return sorted(value)
    raise ValueError("COMMANDS set not found")


def cli_flags(source):
    tree = ast.parse(source)
    return sorted({
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.fullmatch(r"--[a-z][a-z-]*", node.value)
    })


def mcp_tool_names(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "MCPServer":
            continue
        for method in node.body:
            if not isinstance(method, ast.FunctionDef) or \
                    method.name != "tools":
                continue
            names = []
            for child in ast.walk(method):
                if not isinstance(child, ast.Dict):
                    continue
                for key, value in zip(child.keys, child.values):
                    if isinstance(key, ast.Constant) and key.value == "name" \
                            and isinstance(value, ast.Constant) \
                            and isinstance(value.value, str):
                        names.append(value.value)
            return sorted(set(names))
    raise ValueError("MCPServer.tools not found")


def environment_names(source):
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Attribute) and \
                    isinstance(owner.value, ast.Name) and \
                    owner.value.id == "os" and owner.attr == "environ" and \
                    node.args and isinstance(node.args[0], ast.Constant) and \
                    isinstance(node.args[0].value, str):
                names.add(node.args[0].value)
        if isinstance(node, ast.Subscript) and \
                isinstance(node.value, ast.Attribute) and \
                isinstance(node.value.value, ast.Name) and \
                node.value.value.id == "os" and \
                node.value.attr == "environ":
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
    return sorted(
        name for name in names if re.fullmatch(r"MIND_[A-Z0-9_]+", name))


def discovered_test_ids():
    script = """
import json
import unittest

tests = []

def add(item):
    if isinstance(item, unittest.TestSuite):
        for child in item:
            add(child)
    else:
        tests.append(item.id())

add(unittest.defaultTestLoader.discover("tests"))
print(json.dumps(tests))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_count():
    return len(discovered_test_ids())


def mutation_excluded_test_prefixes():
    source = (ROOT / "bench" / "mutate.py").read_text("utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
                isinstance(target, ast.Name)
                and target.id == "MUTATION_EXCLUDED_TEST_PREFIXES"
                for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, tuple) and all(
                    isinstance(prefix, str) for prefix in value):
                return value
    raise ValueError("mutation exclusion contract not found")


def computed_facts():
    project = json.loads(PROJECT_PATH.read_text("utf-8"))
    source = (ROOT / "mind.py").read_text("utf-8")
    version_match = re.search(
        r'^__version__\s*=\s*"([^"]+)"', source, re.MULTILINE)
    protocol_match = re.search(
        r'^MCP_PROTOCOL_VERSION\s*=\s*"([^"]+)"',
        source, re.MULTILINE)
    if not version_match or not protocol_match:
        raise ValueError("version constants not found")
    manifest = json.loads(
        (ROOT / "src" / "mind" / "source.json").read_text("utf-8"))
    facts = {
        "format": 1,
        "development_version": version_match.group(1),
        "development_status": project["development_status"],
        "stable_release": project["stable_release"],
        "test_count": test_count(),
        "artifact_sha256": sha256(ROOT / "mind.py"),
        "artifact_bytes": (ROOT / "mind.py").stat().st_size,
        "artifact_lines": len(source.splitlines()),
        "source_fragments": len(manifest["fragments"]),
        "commands": command_names(source),
        "cli_flags": cli_flags(source),
        "mcp_tools": mcp_tool_names(source),
        "environment_variables": environment_names(source),
        "mcp_protocol_version": protocol_match.group(1),
        "supported_python": project["supported_python"],
        "ci_cells": project["ci_cells"],
        "capabilities": project["capabilities"],
        "public_results": project["public_results"],
    }
    return facts


def facts_block(facts, language):
    stable = facts["stable_release"]
    if language == "ar":
        status = {
            "preview": "معاينة قبل الإصدار",
            "stable": "مستقرة",
        }.get(facts["development_status"], facts["development_status"])
        body = [
            BEGIN,
            "- النسخة الحالية: `%s`، وحالتها %s."
            % (facts["development_version"], status),
            "- الإصدار المستقر: `%s`، وبصمة ملفه `%s`."
            % (stable["version"], stable["mind_sha256"]),
            "- الاختبارات المكتشفة آليا: **%d**."
            % facts["test_count"],
            "- التوزيع: **%d** مجالات مصدرية تبني ملفا واحدا حتميا؛ "
            "بصمة الملف الحالي `%s`."
            % (facts["source_fragments"], facts["artifact_sha256"]),
            "- مصفوفة التكامل: **%d** خلايا لأنظمة وإصدارات بايثون."
            % facts["ci_cells"],
            "- سطر الأوامر: **%d** أمرا؛ خادم البروتوكول: **%d** أداة."
            % (len(facts["commands"]), len(facts["mcp_tools"])),
            END,
        ]
    else:
        body = [
            BEGIN,
            "- Development version: `%s` (%s)."
            % (facts["development_version"], facts["development_status"]),
            "- Stable release: `%s`; pinned `mind.py` SHA-256 `%s`."
            % (stable["version"], stable["mind_sha256"]),
            "- Discovered tests: **%d**."
            % facts["test_count"],
            "- Distribution: **%d** source-domain fragments build one "
            "deterministic file; current artifact SHA-256 `%s`."
            % (facts["source_fragments"], facts["artifact_sha256"]),
            "- CI matrix: **%d** operating-system/Python cells."
            % facts["ci_cells"],
            "- Command line: **%d** commands; protocol server: **%d** tools."
            % (len(facts["commands"]), len(facts["mcp_tools"])),
            END,
        ]
    return "\n".join(body)


def replace_block(text, replacement, begin=BEGIN, end=END):
    pattern = re.compile(
        re.escape(begin) + r".*?" + re.escape(end),
        re.DOTALL)
    if not pattern.search(text):
        raise ValueError("document is missing generated facts markers")
    return pattern.sub(replacement, text, count=1)


def load_public_results(facts):
    results = {}
    for relative in facts["public_results"]:
        path = ROOT / relative
        if path.is_file():
            results[relative] = json.loads(path.read_text("utf-8"))
    return results


def benchmark_block(facts, language):
    results = load_public_results(facts)
    missing = [
        path for path in facts["public_results"]
        if path not in results
    ]
    if missing:
        raise ValueError(
            "cannot render benchmark block; missing: %s"
            % ", ".join(missing))
    get = results.__getitem__
    offline_path = "bench/results/longmemeval-offline-v7-dev.json"
    bm25_path = "bench/results/longmemeval-bm25-v7-dev.json"
    concept_path = "bench/results/longmemeval-concept-v7-dev.json"
    paraphrase_path = "bench/results/paraphrase-v7-dev.json"
    bulk_path = "bench/results/bulk-v7-dev.json"
    autonomy_path = "bench/results/autonomy-five-year-v7-dev.json"
    mutation_mind_path = "bench/results/mutation-mind-v7-dev.json"
    mutation_long_path = (
        "bench/results/mutation-longmemeval-v7-dev.json")
    offline = get(offline_path)
    bm25 = get(bm25_path)
    concept = get(concept_path)
    paraphrase = get(paraphrase_path)
    bulk = get(bulk_path)
    autonomy = get(autonomy_path)
    mutation_mind = get(mutation_mind_path)
    mutation_long = get(mutation_long_path)

    def long_metric(report):
        return "%.3f / %.3f / %.3f" % (
            report["evidence_at_1_rate"],
            report["evidence_at_k_rate"],
            report["answer_string_at_k_rate"],
        )

    def mutation_metric(report):
        summary = report["summary"]
        if language == "ar":
            return "%d/%d مقتولة (%.1f%%)؛ %d ناجية" % (
                summary["killed"],
                summary["classified"],
                summary["kill_rate"] * 100.0,
                summary["survived"],
            )
        return "%d/%d killed (%.1f%%); %d survived" % (
            summary["killed"], summary["classified"],
            summary["kill_rate"] * 100.0, summary["survived"])

    if language == "ar":
        rows = [
            ("خط أساس الترجيح الاحتمالي",
             long_metric(bm25), bm25_path),
            ("مايند المحلي", long_metric(offline), offline_path),
            ("مايند مع الخادم المفاهيمي",
             long_metric(concept), concept_path),
            ("فخاخ إعادة الصياغة",
             "المحلي %d/%d؛ الخادم %d/%d" % (
                 paraphrase["offline"]["correct"],
                 paraphrase["offline"]["cases"],
                 paraphrase["server"]["correct"],
                 paraphrase["server"]["cases"]),
             paraphrase_path),
            ("إدخال عشرة آلاف حقيقة",
             "التزام واحد؛ تحسن محافظ %.1f ضعفا" % (
                 bulk["conservative_lower_bound_speedup"]),
             bulk_path),
            ("الأفق التلقائي",
             "%d جلسة و%d يوما محاكى" % (
                 autonomy["sessions"]["sessions"],
                 autonomy["horizon"]["simulated_days"]),
             autonomy_path),
            ("طفرات الملف الموزع",
             mutation_metric(mutation_mind), mutation_mind_path),
            ("طفرات مقياس الذاكرة الطويلة",
             mutation_metric(mutation_long), mutation_long_path),
        ]
        header = (
            BENCH_BEGIN + "\n"
            "| النتيجة | الدليل الحالي | المحضر الخام |\n"
            "|---|---:|---|")
    else:
        rows = [
            ("BM25 baseline", long_metric(bm25), bm25_path),
            ("mind offline", long_metric(offline), offline_path),
            ("mind with concept sidecar",
             long_metric(concept), concept_path),
            ("Paraphrase traps",
             "offline %d/%d; sidecar %d/%d" % (
                 paraphrase["offline"]["correct"],
                 paraphrase["offline"]["cases"],
                 paraphrase["server"]["correct"],
                 paraphrase["server"]["cases"]),
             paraphrase_path),
            ("10,000-fact bulk ingest",
             "one commit; conservative %.1fx speedup" % (
                 bulk["conservative_lower_bound_speedup"]),
             bulk_path),
            ("Auto-first horizon",
             "%d sessions and %d simulated days" % (
                 autonomy["sessions"]["sessions"],
                 autonomy["horizon"]["simulated_days"]),
             autonomy_path),
            ("Single-file mutations",
             mutation_metric(mutation_mind), mutation_mind_path),
            ("LongMemEval-harness mutations",
             mutation_metric(mutation_long), mutation_long_path),
        ]
        header = (
            BENCH_BEGIN + "\n"
            "| Result | Current evidence | Raw report |\n"
            "|---|---:|---|")
    lines = [header]
    lines.extend(
        "| %s | %s | [`%s`](%s) |" % (
            label, value, Path(path).name, path)
        for label, value, path in rows
    )
    lines.append(
        "\nOn this subset, BM25 leads both evidence metrics. This "
        "benchmark does not measure graph traversal, temporal validity, "
        "contradiction handling, or lifecycle operations, so it does not "
        "establish overall product superiority." if language == "en" else
        "\nفي هذه العينة يتفوق خط أساس بي إم خمسة وعشرين في مقياسي "
        "دليل الأول ودليل الخمسة. لا يقيس هذا التقييم اجتياز الرسم أو "
        "الصلاحية الزمنية أو معالجة التناقض أو عمليات دورة الحياة، ولذلك "
        "لا يثبت تفوقا شاملا لأي منتج.")
    lines.append(
        "\nLongMemEval values are evidence@1 / evidence@5 / "
        "answer-string@5." if language == "en" else
        "\nقيم الذاكرة الطويلة هي دليل الأول ثم دليل الخمسة ثم نص "
        "الإجابة في الخمسة.")
    lines.append(BENCH_END)
    return "\n".join(lines)


def section_ids(path):
    return re.findall(
        r"<!-- mind:section ([a-z0-9-]+) -->",
        Path(path).read_text("utf-8"))


def section_text(path, section_id):
    text = Path(path).read_text("utf-8")
    start = "<!-- mind:section %s -->" % section_id
    if start not in text:
        return ""
    tail = text.split(start, 1)[1]
    return tail.split("<!-- mind:section ", 1)[0]


def section_commands(path):
    text = section_text(path, "commands")
    return set(re.findall(
        r"^([a-z][a-z-]*)(?:\s|$)", text, re.MULTILINE))


def section_flags(path):
    return set(re.findall(
        r"--[a-z][a-z-]*", section_text(path, "commands")))


def validate_docs(facts):
    errors = []
    mutation_prefixes = mutation_excluded_test_prefixes()
    mutation_tests = sum(
        not test_id.startswith(mutation_prefixes)
        for test_id in discovered_test_ids())
    english_sections = section_ids(ROOT / "README.md")
    arabic_sections = section_ids(ROOT / "README.ar.md")
    if not english_sections or english_sections != arabic_sections:
        errors.append("English/Arabic section markers differ")
    source = (ROOT / "mind.py").read_text("utf-8")
    known_env = set(facts["environment_variables"])
    known_commands = set(facts["commands"])
    known_flags = set(facts["cli_flags"])
    for path in (ROOT / "README.md", ROOT / "README.ar.md"):
        commands = section_commands(path)
        if commands != known_commands:
            errors.append(
                "%s command surface differs: missing=%s extra=%s" % (
                    path.name,
                    ",".join(sorted(known_commands - commands)) or "none",
                    ",".join(sorted(commands - known_commands)) or "none",
                ))
        flags = section_flags(path)
        unknown_flags = sorted(flags - known_flags)
        if unknown_flags:
            errors.append(
                "%s command surface has unknown flags: %s" % (
                    path.name, ", ".join(unknown_flags)))
        documented_env = set(re.findall(
            r"\bMIND_[A-Z0-9_]+\b", path.read_text("utf-8")))
        if documented_env != known_env:
            errors.append(
                "%s environment reference differs: missing=%s extra=%s" % (
                    path.name,
                    ",".join(sorted(known_env - documented_env)) or "none",
                    ",".join(sorted(documented_env - known_env)) or "none",
                ))
    for path in DOCS:
        text = path.read_text("utf-8")
        documented_env = set(re.findall(
            r"\bMIND_[A-Z0-9_]+\b", text))
        unknown_env = sorted(documented_env - known_env)
        if unknown_env:
            errors.append(
                "%s documents unknown env vars: %s"
                % (path.name, ", ".join(unknown_env)))
        documented_commands = set(re.findall(
            r"\bmind\.py[ \t]+([a-z][a-z-]*)", text))
        unknown_commands = sorted(
            documented_commands - known_commands)
        if unknown_commands:
            errors.append(
                "%s documents unknown commands: %s"
                % (path.name, ", ".join(unknown_commands)))
    security = (ROOT / "SECURITY.md").read_text("utf-8")
    for forbidden in (
            "No network access, no spawned processes",
            "nothing is ever executed",
            "~3,000 lines"):
        if forbidden in security:
            errors.append(
                "SECURITY.md retains false claim: %s" % forbidden)
    for relative in facts["public_results"]:
        path = ROOT / relative
        if not path.is_file():
            errors.append("missing public result: %s" % relative)
            continue
        try:
            text = path.read_text("utf-8")
            data = json.loads(text)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(
                "invalid public result %s: %s" % (relative, exc))
            continue
        for pattern in PERSONAL_PATTERNS:
            if pattern.search(text):
                errors.append(
                    "personal data pattern in %s" % relative)
        provenance = data.get("provenance")
        if not isinstance(provenance, dict):
            errors.append("missing provenance in %s" % relative)
            continue
        if provenance.get("dirty") is not False:
            errors.append("result is not from a clean tree: %s" % relative)
        if not re.fullmatch(
                r"[0-9a-f]{40}", str(provenance.get("commit", ""))):
            errors.append("invalid commit provenance in %s" % relative)
        if provenance.get("mind_sha256") != facts["artifact_sha256"]:
            errors.append("artifact hash mismatch in %s" % relative)
        if "--json-out" in str(data.get("command", "")):
            errors.append("output path retained in command: %s" % relative)
        benchmark = data.get("benchmark")
        if isinstance(benchmark, str) and benchmark.startswith(
                "deterministic-mutation-"):
            if benchmark != "deterministic-mutation-v3" or \
                    data.get("format") != 2:
                errors.append(
                    "obsolete mutation report format in %s" % relative)
            summary = data.get("summary", {})
            baseline = data.get("baseline", {})
            if baseline.get("outcome") != "survived":
                errors.append("mutation baseline is not green: %s" % relative)
            suite = data.get("suite", {})
            if suite.get("excluded_test_prefixes") != list(
                    mutation_prefixes) or not isinstance(
                        suite.get("exclusion_reason"), str):
                errors.append(
                    "mutation suite exclusion contract mismatch in %s"
                    % relative)
            if baseline.get("tests_run") != mutation_tests:
                errors.append(
                    "mutation baseline test count mismatch in %s"
                    % relative)
            if summary.get("timed_out") or \
                    summary.get("infrastructure_error") or \
                    summary.get("invalid"):
                errors.append(
                    "invalid mutation outcomes in %s" % relative)
            corpus = data.get("corpus", {})
            if corpus.get("id") != "mind-ast-operators-v2" or \
                    not re.fullmatch(
                        r"[0-9a-f]{64}",
                        str(corpus.get("manifest_sha256", ""))):
                errors.append(
                    "invalid mutation corpus identity in %s" % relative)
            if corpus.get("generator_sha256") != sha256(
                    ROOT / "bench" / "mutate.py"):
                errors.append(
                    "stale mutation generator identity in %s" % relative)
            mutants = data.get("mutants", [])
            rechecks = [
                mutant for mutant in mutants
                if isinstance(mutant, dict)
                and "initial_attempt" in mutant
            ]
            reclassified = [
                mutant for mutant in rechecks
                if mutant.get("reclassified_parallel_noise") is True
            ]
            if summary.get("candidate_rechecks") != len(rechecks) or \
                    summary.get("parallel_noise_reclassified") != len(
                        reclassified):
                errors.append(
                    "mutation confirmation accounting mismatch in %s"
                    % relative)
            if any(
                    mutant.get("outcome") == "killed"
                    and mutant.get("execution_mode") not in (
                        "isolated", "isolated_confirmation")
                    for mutant in mutants
                    if isinstance(mutant, dict)):
                errors.append(
                    "unconfirmed parallel kill in %s" % relative)
            if summary.get("attempted") != data.get("sample_size") or \
                    summary.get("classified") != data.get("sample_size"):
                errors.append(
                    "mutation sample is not fully classified in %s"
                    % relative)
    manifest = json.loads(
        (ROOT / "bench" / "manifests" /
         "longmemeval.json").read_text("utf-8"))
    results = load_public_results(facts)
    for relative in (
            "bench/results/longmemeval-offline-v7-dev.json",
            "bench/results/longmemeval-bm25-v7-dev.json",
            "bench/results/longmemeval-concept-v7-dev.json"):
        report = results.get(relative)
        if report and (
                report.get("dataset_sha256") != manifest["sha256"]
                or report.get("dataset_revision") != manifest["revision"]):
            errors.append(
                "LongMemEval input identity mismatch in %s" % relative)
        if report and (
                len(report.get("selected_question_ids", []))
                != report.get("selected_questions")
                or len(report.get("evaluated_question_ids", []))
                != report.get("evaluated")):
            errors.append(
                "LongMemEval question identities incomplete in %s"
                % relative)
    offline = results.get(
        "bench/results/longmemeval-offline-v7-dev.json")
    bm25 = results.get(
        "bench/results/longmemeval-bm25-v7-dev.json")
    concept = results.get(
        "bench/results/longmemeval-concept-v7-dev.json")
    paraphrase = results.get(
        "bench/results/paraphrase-v7-dev.json")
    bulk = results.get("bench/results/bulk-v7-dev.json")
    autonomy = results.get(
        "bench/results/autonomy-five-year-v7-dev.json")
    if offline and offline.get("backend", {}).get("mode") != "offline":
        errors.append("offline LongMemEval result used another backend")
    if bm25 and bm25.get("backend", {}).get("mode") != "bm25":
        errors.append("BM25 LongMemEval result used another backend")
    if concept and (
            concept.get("backend", {}).get("mode") != "server"
            or concept.get("backend", {}).get("fallbacks") != 0):
        errors.append("concept LongMemEval result degraded or fell back")
    if paraphrase and (
            paraphrase.get("server", {}).get("accuracy", 0.0) < 0.9
            or paraphrase.get("server", {}).get(
                "p95_latency_ms", float("inf")) > 25.0):
        errors.append("paraphrase acceptance target not met")
    if bulk and (
            bulk.get("records") != 10_000
            or bulk.get("conservative_lower_bound_speedup", 0.0) < 50.0
            or bulk.get("batch_counts") != {
                "commits": 1,
                "journal_batches": 1,
                "signal_batches": 1,
            }):
        errors.append("bulk-ingest acceptance target not met")
    if autonomy and (
            autonomy.get("sessions", {}).get("accepted") != 30
            or autonomy.get("horizon", {}).get(
                "simulated_days", 0) < 1825
            or not autonomy.get("horizon", {}).get("archive_rotated")
            or not autonomy.get("horizon", {}).get("doctor_ok")):
        errors.append("five-year autonomy acceptance target not met")
    if source.count(BEGIN):
        errors.append("generated documentation marker leaked into source")
    if source.count(BENCH_BEGIN):
        errors.append("generated benchmark marker leaked into source")
    return errors


def update():
    facts = computed_facts()
    FACTS_PATH.write_text(
        json.dumps(facts, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    for path in DOCS:
        language = "ar" if path.name.endswith(".ar.md") else "en"
        text = path.read_text("utf-8")
        path.write_text(
            replace_block(text, facts_block(facts, language)) + (
                "" if text.endswith("\n") else "\n"),
            encoding="utf-8",
        )
        if path.name.startswith("README"):
            text = path.read_text("utf-8")
            path.write_text(
                replace_block(
                    text,
                    benchmark_block(facts, language),
                    BENCH_BEGIN,
                    BENCH_END,
                ),
                encoding="utf-8",
            )
    return facts


def check():
    expected = computed_facts()
    if not FACTS_PATH.is_file():
        return ["docs/facts.json is missing"]
    actual = json.loads(FACTS_PATH.read_text("utf-8"))
    errors = []
    if actual != expected:
        errors.append("docs/facts.json is stale")
    for path in DOCS:
        language = "ar" if path.name.endswith(".ar.md") else "en"
        text = path.read_text("utf-8")
        try:
            rendered = replace_block(
                text, facts_block(expected, language))
        except ValueError as exc:
            errors.append("%s: %s" % (path.name, exc))
            continue
        if rendered != text:
            errors.append("%s generated facts block is stale" % path.name)
        if path.name.startswith("README"):
            try:
                rendered_bench = replace_block(
                    text,
                    benchmark_block(expected, language),
                    BENCH_BEGIN,
                    BENCH_END,
                )
            except ValueError as exc:
                errors.append("%s: %s" % (path.name, exc))
            else:
                if rendered_bench != text:
                    errors.append(
                        "%s generated benchmark block is stale"
                        % path.name)
    build = subprocess.run(
        [sys.executable, "tools/build_single.py", "--check"],
        cwd=ROOT, capture_output=True, text=True)
    if build.returncode:
        errors.append("single-file artifact is stale")
    errors.extend(validate_docs(expected))
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("update", "check"), nargs="?", default="check")
    args = parser.parse_args(argv)
    if args.mode == "update":
        facts = update()
        print("updated claims for %d tests" % facts["test_count"])
        return 0
    errors = check()
    if errors:
        for error in errors:
            print("claims error: %s" % error)
        return 1
    print("claims and documentation are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
