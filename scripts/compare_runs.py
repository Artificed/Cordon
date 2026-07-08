#!/usr/bin/env python3
"""Compare several experiment runs (e.g. a planner-model ladder) side by side.

Each run is analyzed independently; the headline metrics are tabulated as
(run, arm) rows so the model-capability ladder is readable at a glance: does
the ungoverned baseline get safer as the planner improves, while Cordon holds?

Usage:
  python scripts/compare_runs.py \
      nano=experiments/full-nano/records.jsonl \
      mini=experiments/full-mini/records.jsonl \
      gpt55=experiments/full-gpt55/records.jsonl
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cordon.analysis import analyze, load_records
from cordon.dataset import load_dataset

HEADLINE = [
    ("decision accuracy", "decision_correct"),
    ("harmful exec", "harmful_executed"),
    ("policy-viol exec", "policy_violation_executed"),
    ("unsafe invocation", "unsafe_invocation"),
    ("blocked-safe", "safe_blocked"),
    ("task completion", "safe_completed"),
    ("drift correction", "drift_corrected"),
]
ARMS = ["baseline", "prompt_guardrails", "cordon"]


def metric_lookup(analysis: dict) -> dict:
    out = {}
    for row in analysis["metrics"]:
        if "label" not in row:
            continue
        for arm, v in row["values"].items():
            out[(row["label"], arm)] = v["rate"]
    return out


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    runs = []
    for arg in sys.argv[1:]:
        label, path = arg.split("=", 1)
        runs.append((label, Path(path)))

    dataset = load_dataset()
    analyses = {label: metric_lookup(analyze(load_records(path), dataset))
                for label, path in runs}
    # analysis.py labels: map short -> full
    label_map = {
        "decision_correct": "decision accuracy",
        "harmful_executed": "harmful-action execution rate",
        "policy_violation_executed": "policy-violation execution rate",
        "unsafe_invocation": "unsafe invocation rate",
        "safe_blocked": "blocked-safe-task rate",
        "safe_completed": "task completion rate (safe)",
        "drift_corrected": "drift correction success",
    }

    for metric_label, fact in HEADLINE:
        print(f"\n### {metric_label}")
        header = f"{'run':<10}" + "".join(f"{a:>20}" for a in ARMS)
        print(header)
        print("-" * len(header))
        for run_label, _ in runs:
            lut = analyses[run_label]
            cells = []
            for arm in ARMS:
                rate = lut.get((label_map[fact], arm))
                cells.append("n/a" if rate is None else f"{rate*100:.1f}%")
            print(f"{run_label:<10}" + "".join(f"{c:>20}" for c in cells))


if __name__ == "__main__":
    main()
