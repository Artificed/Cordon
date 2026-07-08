#!/usr/bin/env python3
"""Pre-generate and cache LLM plans for a model + prompt variants.

Runs the planner concurrently over every (scenario, rep) and writes the plans
to experiments/plans/<model>/<variant>.jsonl so the enforcement pipeline can
replay them without further API calls. Resume-safe: re-running only fills
missing (scenario, rep) keys.

Usage:
  python scripts/pregenerate_plans.py --model gpt-5-mini --variants core promptguard --reps 3
  python scripts/pregenerate_plans.py --model gpt-5-mini --variants core \
      --scenarios S001,S061,S091 --reps 1 --out experiments/pilot
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cordon.config import load_config
from cordon.dataset import load_dataset
from cordon.planner import OpenAIPlanner
from cordon.plancache import cache_summary, pregenerate_plans


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-generate LLM plans into a cache")
    parser.add_argument("--model", required=True)
    parser.add_argument("--variants", nargs="+", default=["core", "promptguard"],
                        choices=["core", "promptguard"])
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--scenarios", default=None,
                        help="comma-separated scenario ids (subset)")
    parser.add_argument("--out", default="experiments",
                        help="root under which plans/<model>/<variant>.jsonl is written")
    parser.add_argument("--concurrency", type=int, default=None)
    args = parser.parse_args()

    config = load_config()
    dataset = load_dataset()
    concurrency = args.concurrency or config.planner.concurrency
    scenarios = None
    if args.scenarios:
        wanted = set(args.scenarios.split(","))
        scenarios = [s for s in dataset.scenarios if s.scenario_id in wanted]

    for variant in args.variants:
        planner = OpenAIPlanner(
            dataset.actions, dataset.policies, model=args.model, variant=variant,
            temperature=config.planner.temperature,
            reasoning_effort=config.planner.reasoning_effort,
            max_output_tokens=config.planner.max_output_tokens,
            timeout_s=config.planner.timeout_s,
            max_retries=config.planner.max_retries,
        )
        print(f"[{args.model}/{variant}] generating (reps={args.reps}, "
              f"concurrency={concurrency}) ...", flush=True)
        pregenerate_plans(dataset, planner, reps=args.reps, out_root=args.out,
                          concurrency=concurrency, scenarios=scenarios)
        summary = cache_summary(args.out, args.model, variant)
        print(f"[{args.model}/{variant}] {summary}", flush=True)


if __name__ == "__main__":
    main()
