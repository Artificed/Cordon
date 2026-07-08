#!/usr/bin/env python3
"""Dataset executability dry-run against LocalStack.

For every scenario: reset the environment, load the initial state, inject
drift if specified, then verify by observation that (a) every initial
resource exists with the attributes the scenario recorded and (b) the drift
attribute reads back as the drifted actual value. This substantiates the
"all scenarios executable" validation claim for the experiment environment.

Usage: python scripts/dryrun_dataset.py
"""

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cordon.dataset import load_dataset
from cordon.env.localstack import LocalStackEnvironment
from cordon.scoring import ENV_IGNORED_ATTRS


def verify_scenario(env: LocalStackEnvironment, scenario) -> list[str]:
    problems = []
    env.load_initial_state(scenario.initial_state.resources)
    if scenario.drift_spec is not None:
        env.apply_drift(scenario.drift_spec)

    observed = env.observe()
    drift = scenario.drift_spec
    for resource in scenario.initial_state.resources:
        seen = observed.get(resource.resource_id)
        if seen is None:
            problems.append(f"{resource.resource_id}: not observable after load")
            continue
        for key, value in resource.attributes.items():
            if key in ENV_IGNORED_ATTRS:
                continue
            expected = value
            if drift and drift.resource_id == resource.resource_id and key == drift.attribute:
                expected = drift.actual_value  # environment must show the drifted value
            got = seen["attributes"].get(key)
            if got != expected:
                problems.append(f"{resource.resource_id}.{key}: expected {expected!r}, got {got!r}")

    extra = set(observed) - {r.resource_id for r in scenario.initial_state.resources}
    if extra:
        problems.append(f"unexpected resources after load: {sorted(extra)}")
    return problems


def main() -> int:
    dataset = load_dataset()
    env = LocalStackEnvironment()
    started = time.perf_counter()
    ok_per_category: Counter = Counter()
    failures: dict[str, list[str]] = {}

    for i, scenario in enumerate(dataset.scenarios, 1):
        problems = verify_scenario(env, scenario)
        if problems:
            failures[scenario.scenario_id] = problems
        else:
            ok_per_category[scenario.category.value] += 1
        if i % 40 == 0:
            print(f"  ... {i}/240 checked", flush=True)

    env.cleanup()
    elapsed = time.perf_counter() - started

    print(f"\nDataset dry-run against LocalStack ({elapsed:.0f}s)")
    print(f"{'category':<28}{'loadable':>10}")
    print("-" * 38)
    for category, count in sorted(ok_per_category.items()):
        print(f"{category:<28}{count:>7}/30")
    total_ok = sum(ok_per_category.values())
    print("-" * 38)
    print(f"{'TOTAL':<28}{total_ok:>6}/240")

    if failures:
        print(f"\n{len(failures)} scenario(s) FAILED:")
        for sid, problems in list(failures.items())[:20]:
            for p in problems:
                print(f"  {sid}: {p}")
        return 1
    print("\nAll initial states loadable; all drift specs injectable and observable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
