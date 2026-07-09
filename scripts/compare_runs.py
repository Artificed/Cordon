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

# (display name, exact metric label emitted by cordon.analysis)
# Security metrics first: they are outcome-based and structurally fair to every
# arm. Decision accuracy is reported enforcement-only, because baseline and
# prompt-guardrails cannot emit requires_approval/reconcile at all.
HEADLINE = [
    ("harmful exec", "harmful-action execution rate"),
    ("policy-viol exec", "policy-violation execution rate"),
    ("unsafe invocation", "unsafe invocation rate"),
    ("blocked-safe", "blocked-safe-task rate"),
    ("decision acc (enforcement-only)", "decision accuracy (enforcement-only)"),
    ("decision acc (all cats, unfair)", "decision accuracy (all categories)*"),
    ("task completion", "task completion rate (safe)"),
    ("drift correction", "drift correction success"),
]
ARMS = ["baseline", "prompt_guardrails", "cordon"]


def metric_lookup(analysis: dict) -> dict:
    """(metric label, arm) -> {rate, ci, n} for every metric row."""
    out = {}
    for row in analysis["metrics"]:
        if "label" not in row:
            continue
        for arm, v in row["values"].items():
            out[(row["label"], arm)] = v
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

    for display, metric in HEADLINE:
        print(f"\n### {display}")
        header = f"{'run':<10}" + "".join(f"{a:>20}" for a in ARMS)
        print(header)
        print("-" * len(header))
        for run_label, _ in runs:
            lut = analyses[run_label]
            cells = []
            for arm in ARMS:
                v = lut.get((metric, arm))
                if v is None or v["rate"] is None:
                    cells.append("n/a")
                else:
                    lo, hi = v["ci"]
                    cells.append(f"{v['rate']*100:.1f}% [{lo*100:.0f},{hi*100:.0f}]")
            print(f"{run_label:<10}" + "".join(f"{c:>20}" for c in cells))
    print("\nBrackets: 95% cluster-bootstrap CI, resampling scenarios (the independent unit).")
    print("Decision accuracy over ALL categories is not a fair arm-vs-arm comparison: only")
    print("Cordon can emit requires_approval/reconcile. Use the enforcement-only row.")


if __name__ == "__main__":
    main()
