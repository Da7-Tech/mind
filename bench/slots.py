#!/usr/bin/env python3
"""Labeled slot-conflict precision and recall benchmark."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mind import (  # noqa: E402
    CORTEX_DIR, DREAMS_DIR, GRAPH_FILE, Cortex, Dreamer, Hippocampus)
from bench.provenance import (  # noqa: E402
    repo_provenance, reproducible_command)


def cases():
    values = (
        ("database", "engine", "postgres", "mysql"),
        ("deployment", "strategy", "blue green", "rolling"),
        ("backup", "region", "helsinki", "frankfurt"),
        ("runtime", "version", "python three twelve", "python three fourteen"),
        ("cache", "backend", "redis", "memcached"),
    )
    result = []
    for index in range(25):
        entity, attr, first, second = values[index % len(values)]
        entity = "%s-%02d" % (entity, index)
        result.append({
            "expected": True,
            "left": ("%s uses %s" % (entity, first), entity, attr),
            "right": ("%s uses %s" % (entity, second), entity, attr),
        })
    for index in range(25):
        entity, attr, first, second = values[index % len(values)]
        left_entity = "%s-negative-%02d" % (entity, index)
        if index % 2:
            right_entity = left_entity
            right_attr = attr + "-secondary"
        else:
            right_entity = left_entity + "-other"
            right_attr = attr
        result.append({
            "expected": False,
            "left": (
                "%s uses %s" % (left_entity, first),
                left_entity,
                attr,
            ),
            "right": (
                "%s uses %s" % (right_entity, second),
                right_entity,
                right_attr,
            ),
        })
    return result


def evaluate():
    root = Path(tempfile.mkdtemp(prefix="mind-slots-"))
    true_positive = false_positive = false_negative = true_negative = 0
    try:
        for index, case in enumerate(cases()):
            case_root = root / ("%02d" % index)
            case_root.mkdir()
            (case_root / CORTEX_DIR).mkdir()
            (case_root / DREAMS_DIR).mkdir()
            hippo = Hippocampus(case_root / GRAPH_FILE)
            left_text, left_entity, left_attr = case["left"]
            right_text, right_entity, right_attr = case["right"]
            left_id = hippo.remember(left_text, metadata={
                "entity": left_entity,
                "attr": left_attr,
            })
            right_id = hippo.remember(right_text, metadata={
                "entity": right_entity,
                "attr": right_attr,
            })
            Dreamer(
                case_root,
                hippo,
                Cortex(case_root / CORTEX_DIR),
            ).dream()
            detected = (
                hippo.edges.get(left_id, {}).get(
                    right_id, {}).get("conflict_kind")
                == "slot"
            )
            if case["expected"] and detected:
                true_positive += 1
            elif case["expected"]:
                false_negative += 1
            elif detected:
                false_positive += 1
            else:
                true_negative += 1
    finally:
        shutil.rmtree(root, ignore_errors=True)
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive else 0.0)
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative else 0.0)
    return {
        "benchmark": "slot-conflict-classification-v1",
        "command": reproducible_command(),
        "provenance": repo_provenance(("bench/slots.py",)),
        "cases": len(cases()),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": precision,
        "recall": recall,
    }


def main():
    report = evaluate()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if (
        report["precision"] >= 0.8
        and report["recall"] >= 0.8
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
