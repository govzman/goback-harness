"""Recompute the checkpoint report from the committed raw results."""

import json
import math
import statistics
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


RESULTS = Path(__file__).resolve().parent


def read_jsonl(filename):
    with (RESULTS / filename).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def score(rows, key="pass"):
    return {"passed": sum(row[key] for row in rows), "total": len(rows)}


def trial_metrics(rows):
    return {
        **score(rows),
        "duration_s": round(sum(row["duration_ms"] for row in rows) / 1000, 3),
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
    }


def sample_metrics(values):
    ordered = sorted(map(Decimal, values))
    precision = Decimal("0.01")
    return {
        "n": len(ordered),
        "median": float(statistics.median(ordered).quantize(precision, rounding=ROUND_HALF_UP)),
        "p95": float(ordered[math.ceil(0.95 * len(ordered)) - 1].quantize(precision, rounding=ROUND_HALF_UP)),
    }


def summarize():
    terra = read_jsonl("terra-final.jsonl")
    retries = read_jsonl("terra-transport-retry.jsonl")
    undo = read_jsonl("undo-comparison.jsonl")
    successful_tasks = {row["task"] for row in terra + retries if row["pass"]}
    with (RESULTS / "wsl-environment.json").open(encoding="utf-8") as stream:
        environment = json.load(stream, parse_float=Decimal)
    return {
        "fixture_smoke": trial_metrics(read_jsonl("fixture-smoke.jsonl")),
        "terra_control": {
            **trial_metrics(terra),
            "groups": {
                name: score([row for row in terra if row["task"].startswith(prefix)])
                for name, prefix in (("course_atomic", "B"), ("course_composite", "C"), ("own", "G"))
            },
        },
        "terra_transport_retries": trial_metrics(retries),
        "terra_unique_tasks_with_retries": {
            "passed": len(successful_tasks),
            "total": len({row["task"] for row in terra}),
            "pending": sorted({row["task"] for row in terra} - successful_tasks),
        },
        "undo_comparison": {
            policy: score([row for row in undo if row["policy"] == policy], "correct")
            for policy in sorted({row["policy"] for row in undo})
        },
        "environment": {metric: sample_metrics(values["samples"]) for metric, values in environment.items()},
    }


if __name__ == "__main__":
    print(json.dumps(summarize(), indent=2))
