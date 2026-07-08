#!/usr/bin/env python3
"""Guardrail ablation study: replay cached plans through reduced control planes.

Each ablation disables exactly one Cordon layer (or reconciliation) and runs
the full 240 scenarios by replaying the already-generated plan cache — no API
calls. Comparing each ablation against full Cordon isolates that layer's
contribution to security and utility. Records are written per ablation as an
arm label like "cordon_no_policy".

Usage:
  python scripts/run_ablations.py --model gpt-5-mini --plans experiments/full \
      --reps 3 --out experiments/full-ablations
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cordon.arms import ALL_LAYERS, run_cordon
from cordon.dataset import dataset_hash, load_dataset
from cordon.env.localstack import LocalStackEnvironment
from cordon.plancache import load_cached_planner

# Each ablation: label -> (enabled layers, reconcile on?)
def build_ablations() -> dict[str, tuple[set[str], bool]]:
    ablations: dict[str, tuple[set[str], bool]] = {
        "cordon_full": (set(ALL_LAYERS), True),
        "cordon_no_reconcile": (set(ALL_LAYERS), False),
    }
    for layer in ALL_LAYERS:
        ablations[f"cordon_no_{layer}"] = (set(ALL_LAYERS) - {layer}, True)
    return ablations


def main() -> None:
    parser = argparse.ArgumentParser(description="Cordon guardrail ablation study")
    parser.add_argument("--model", required=True)
    parser.add_argument("--plans", default="experiments/full")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--out", required=True)
    parser.add_argument("--endpoint", default="http://localhost:4566")
    args = parser.parse_args()

    dataset = load_dataset()
    dataset_sha = dataset_hash()
    planner = load_cached_planner(args.plans, args.model, "core")
    env = LocalStackEnvironment(endpoint_url=args.endpoint)
    ablations = build_ablations()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    records_path = out / "records.jsonl"
    done: set[str] = set()
    if records_path.exists():
        with records_path.open() as f:
            done = {json.loads(line)["run_id"] for line in f if line.strip()}

    (out / "manifest.json").write_text(json.dumps({
        "kind": "ablation", "model": args.model, "plans": str(args.plans),
        "reps": args.reps, "dataset_hash": dataset_sha,
        "ablations": {k: sorted(v[0]) + (["reconcile"] if v[1] else [])
                      for k, v in ablations.items()},
    }, indent=2))

    total = len(dataset.scenarios) * args.reps * len(ablations)
    finished = len(done)
    with records_path.open("a") as sink:
        for scenario in dataset.scenarios:
            for rep in range(args.reps):
                plan, info = planner.plan_for(scenario, rep)
                for label, (enabled, reconcile_on) in ablations.items():
                    run_id = f"{scenario.scenario_id}-{label}-r{rep}"
                    if run_id in done:
                        continue
                    record = run_cordon(
                        scenario, plan.model_copy(deep=True), info.model_copy(),
                        env, dataset.actions, dataset.policies, rep=rep,
                        run_id=run_id, dataset_hash=dataset_sha,
                        prompt_hash=planner.prompt_sha,
                        enabled_layers=enabled, reconcile_enabled=reconcile_on,
                        arm=label)
                    sink.write(record.model_dump_json() + "\n")
                    sink.flush()
                    finished += 1
                    if finished % 200 == 0:
                        print(f"  ... {finished}/{total} ablation runs", flush=True)
    env.cleanup()
    print(f"done: {finished}/{total} records in {records_path}")


if __name__ == "__main__":
    main()
