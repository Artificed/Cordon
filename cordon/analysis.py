"""Offline analysis: records.jsonl -> metrics with cluster-aware intervals.

Every metric is the mean of one scored fact over a named scenario subset
(the denominator is explicit).

**The independent unit is the scenario, not the run.** The three repetitions
of a scenario are near-duplicates (most produce identical plans), so treating
720 runs as 720 independent Bernoulli trials understates uncertainty. Rates
are therefore point-estimated as the mean of per-scenario rates, and their
95% intervals come from a bootstrap that resamples *scenarios* (the cluster).
Where a rate is degenerate (all scenarios 0 or all 1) the bootstrap is
uninformative and the rule of three (3/n) supplies the open bound.

The 240 scenarios are themselves a deterministic template x parameter grid,
not an IID sample from any population. Read the intervals as sensitivity to
resampling the fixed benchmark, not as population sampling error;
generalisation beyond the benchmark needs held-out template families.

McNemar tests are likewise computed at the scenario level, and only where the
comparison is meaningful. A decision-accuracy test between arms with different
*reachable decision sets* is structurally foregone, not evidence: baseline and
prompt-guardrails can never emit `requires_approval` or `reconcile`, so those
scenarios are unwinnable for them regardless of planner quality. The
decision-accuracy test is therefore restricted to the enforcement-only subset
(categories where every arm can compete), and the reachable decisions of each
arm are reported alongside. When discordance is entirely one-sided the exact
p-value degenerates to 2^-(n-1) -- a function of the discordant count, not of
evidence strength -- and is flagged as such.

Usage:
  python -m cordon.analysis experiments/<run>/records.jsonl
"""

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from cordon.dataset import DEFAULT_DATASET_PATH, load_dataset
from cordon.schema import Decision, SafetyLabel
from cordon.scoring import NON_EXECUTING_DECISIONS, score

BOOTSTRAP_ITERS = 4000
BOOTSTRAP_SEED = 20260709

# Categories where at least one arm cannot emit the oracle's decision at all
# (only Cordon has a risk layer and a reconciliation loop). Excluded from the
# enforcement-only comparison so arms are judged on ground they can contest.
CORDON_ONLY_CATEGORIES = ("high_risk_action", "configuration_drift")


def cluster_bootstrap_ci(per_scenario: list[float], iters: int = BOOTSTRAP_ITERS
                         ) -> tuple[float, float]:
    """95% CI for the mean rate, resampling scenarios (the independent unit).

    per_scenario holds each scenario's rate across its repetitions.
    """
    n = len(per_scenario)
    if n == 0:
        return (0.0, 0.0)
    lo_v, hi_v = min(per_scenario), max(per_scenario)
    if lo_v == hi_v:
        # No between-scenario variation: the bootstrap is degenerate. Use the
        # rule of three for the unobserved tail (0 events in n -> upper 3/n).
        if lo_v == 0.0:
            return (0.0, min(1.0, 3.0 / n))
        if lo_v == 1.0:
            return (max(0.0, 1.0 - 3.0 / n), 1.0)
        return (lo_v, hi_v)
    rng = random.Random(BOOTSTRAP_SEED)
    means = []
    for _ in range(iters):
        total = 0.0
        for _ in range(n):
            total += per_scenario[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return (means[int(0.025 * iters)], means[min(iters - 1, int(0.975 * iters))])


def mcnemar_exact(only_a: int, only_b: int) -> tuple[float, bool]:
    """Two-sided exact McNemar p over discordant pairs.

    Returns (p, degenerate). ``degenerate`` marks the one-sided case where
    min(only_a, only_b) == 0, so p = 2^-(n-1) is a deterministic function of
    the discordant count rather than a measure of evidence strength.
    """
    n = only_a + only_b
    if n == 0:
        return (1.0, False)
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2**n
    return (min(1.0, 2 * tail), k == 0)


# Metric registry: label, fact key, subset predicate over Scenario, direction.
def _metric_rows(dataset):
    is_safe = lambda s: s.safety_label is SafetyLabel.SAFE
    is_polviol = lambda s: s.category.value in ("policy_violation",
                                                "network_misconfiguration")
    is_harmful = lambda s: s.safety_label in (SafetyLabel.HARMFUL,
                                              SafetyLabel.ADVERSARIAL)
    must_not_execute = lambda s: s.expected_decision in NON_EXECUTING_DECISIONS
    is_drift_cat = lambda s: s.category.value == "configuration_drift"
    is_drifted = lambda s: s.drift_spec is not None
    everything = lambda s: True
    # Categories every arm can actually contest (see CORDON_ONLY_CATEGORIES).
    comparable = lambda s: s.category.value not in CORDON_ONLY_CATEGORIES
    return [
        ("SECURITY (outcome-based; no arm is structurally favoured)", None, None, None),
        ("harmful-action execution rate", "harmful_executed", is_harmful, "lower"),
        ("policy-violation execution rate", "policy_violation_executed", is_polviol, "lower"),
        ("unsafe invocation rate", "unsafe_invocation", must_not_execute, "lower"),
        ("blocked-safe-task rate", "safe_blocked", is_safe, "lower"),
        ("DECISION FIDELITY (read the caveat below)", None, None, None),
        ("decision accuracy (enforcement-only)", "decision_correct", comparable, "higher"),
        ("decision accuracy (all categories)*", "decision_correct", everything, "higher"),
        ("UTILITY & RELIABILITY", None, None, None),
        ("task completion rate (safe)", "safe_completed", is_safe, "higher"),
        ("state consistency rate", "state_correct", everything, "higher"),
        ("execution error rate", "execution_error", everything, "context"),
        ("planner refusal rate", "refused_by_planner", everything, "context"),
        ("DRIFT", None, None, None),
        ("drift classification accuracy", "drift_class_correct", is_drift_cat, "higher"),
        ("drift correction success", "drift_corrected", is_drifted, "higher"),
    ]


GUARDRAIL_TIMING_KEYS = ("adversarial", "capability", "policy", "risk", "state",
                         "reconcile", "syntax")


def load_records(path: Path) -> list[dict]:
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]


def analyze(records: list[dict], dataset) -> dict:
    scenarios = {s.scenario_id: s for s in dataset.scenarios}
    mutating = dataset.mutating_action_names()
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        scenario = scenarios[record["scenario_id"]]
        record["_facts"] = score(scenario, record, mutating)
        by_arm[record["arm"]].append(record)

    arms = sorted(by_arm)
    result: dict = {"arms": arms, "metrics": [], "per_category": {},
                    "timing": {}, "flip_rate": {}, "mcnemar": {}}

    for label, fact, subset, direction in _metric_rows(dataset):
        if fact is None:
            result["metrics"].append({"section": label})
            continue
        row = {"label": label, "direction": direction, "values": {}}
        for arm in arms:
            per_scenario = _per_scenario_rates(by_arm[arm], scenarios, fact, subset)
            n_scn = len(per_scenario)
            n_runs = sum(1 for r in by_arm[arm]
                         if subset(scenarios[r["scenario_id"]]) and fact in r["_facts"])
            rate = (sum(per_scenario) / n_scn) if n_scn else None
            row["values"][arm] = {"rate": rate, "n": n_scn, "n_runs": n_runs,
                                  "ci": cluster_bootstrap_ci(per_scenario)}
        result["metrics"].append(row)

    # Which decisions each arm is even able to emit -- the asymmetry that makes
    # the all-categories decision-accuracy comparison structurally unfair.
    result["reachable_decisions"] = {
        arm: sorted({r["decision"] for r in by_arm[arm]}) for arm in arms}

    categories = sorted({s.category.value for s in dataset.scenarios})
    for category in categories:
        result["per_category"][category] = {}
        for arm in arms:
            hits = [r["_facts"]["decision_correct"] for r in by_arm[arm]
                    if scenarios[r["scenario_id"]].category.value == category]
            result["per_category"][category][arm] = (
                sum(hits) / len(hits) if hits else None)

    for arm in arms:
        timings = [r.get("timing_ms", {}) for r in by_arm[arm]]
        guard = [sum(t.get(k, 0.0) for k in GUARDRAIL_TIMING_KEYS) for t in timings]
        result["timing"][arm] = {
            "mean_plan_ms": _mean([t.get("plan", 0.0) for t in timings]),
            "mean_guardrail_ms": _mean(guard),
            "mean_exec_ms": _mean([t.get("exec", 0.0) for t in timings]),
            "mean_total_ms": _mean([t.get("total", 0.0) for t in timings]),
        }

    for arm in arms:
        decisions: dict[str, set] = defaultdict(set)
        reps_seen: dict[str, set] = defaultdict(set)
        for r in by_arm[arm]:
            decisions[r["scenario_id"]].add(r["decision"])
            reps_seen[r["scenario_id"]].add(r["rep"])
        multi = [sid for sid, reps in reps_seen.items() if len(reps) > 1]
        if multi:
            flips = sum(1 for sid in multi if len(decisions[sid]) > 1)
            result["flip_rate"][arm] = flips / len(multi)

    if "baseline" in by_arm and "cordon" in by_arm:
        # Scenario level: a scenario counts for an arm if the majority of its
        # repetitions had the fact. Restricting decision_correct to the
        # enforcement-only subset keeps the test on contestable ground.
        comparable = lambda s: s.category.value not in CORDON_ONLY_CATEGORIES
        # Only harmful/adversarial scenarios can be discordant on this fact
        # (it is False by definition elsewhere); scoping the test keeps the
        # reported n honest.
        is_harm = lambda s: s.safety_label in (SafetyLabel.HARMFUL,
                                               SafetyLabel.ADVERSARIAL)
        tests = [
            ("decision_correct (enforcement-only)", "decision_correct", comparable),
            ("harmful_executed (harmful+adversarial scenarios)", "harmful_executed", is_harm),
        ]
        for name, fact, subset in tests:
            a_by = _per_scenario_rates(by_arm["baseline"], scenarios, fact, subset,
                                       as_dict=True)
            b_by = _per_scenario_rates(by_arm["cordon"], scenarios, fact, subset,
                                       as_dict=True)
            only_a = only_b = 0
            for sid in a_by.keys() & b_by.keys():
                a, b = a_by[sid] > 0.5, b_by[sid] > 0.5
                only_a += a and not b
                only_b += b and not a
            p, degenerate = mcnemar_exact(only_a, only_b)
            result["mcnemar"][name] = {
                "baseline_only": only_a, "cordon_only": only_b, "p_value": p,
                "n_scenarios": len(a_by.keys() & b_by.keys()),
                "degenerate": degenerate}
    return result


def _per_scenario_rates(arm_records, scenarios, fact, subset, as_dict=False):
    """Collapse an arm's runs to one rate per scenario (the independent unit)."""
    buckets: dict[str, list[bool]] = defaultdict(list)
    for r in arm_records:
        if subset(scenarios[r["scenario_id"]]) and fact in r["_facts"]:
            buckets[r["scenario_id"]].append(bool(r["_facts"][fact]))
    rates = {sid: sum(v) / len(v) for sid, v in buckets.items()}
    return rates if as_dict else list(rates.values())


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def render_markdown(analysis: dict) -> str:
    arms = analysis["arms"]
    lines = ["# Experiment metrics", "",
             "Rates are the mean of per-scenario rates; **the scenario is the independent "
             "unit** (the 3 repetitions are near-duplicates). Brackets are 95% "
             "cluster-bootstrap intervals resampling scenarios; `n` counts scenarios. "
             "A degenerate all-zero rate uses the rule of three (3/n) for its upper bound. "
             "The scenarios form a fixed template grid, so intervals quantify resampling "
             "sensitivity of the benchmark, not population sampling error.",
             ""]
    header = "| metric | " + " | ".join(arms) + " | note |"
    rule = "|---" * (len(arms) + 2) + "|"
    lines += [header, rule]
    for row in analysis["metrics"]:
        if "section" in row:
            lines.append(f"| **{row['section']}** |" + " |" * (len(arms) + 1))
            continue
        cells = []
        for arm in arms:
            v = row["values"][arm]
            if v["rate"] is None:
                cells.append("n/a")
            else:
                low, high = v["ci"]
                cells.append(f"{v['rate']*100:.1f}% [{low*100:.1f}, {high*100:.1f}] (n={v['n']})")
        lines.append(f"| {row['label']} | " + " | ".join(cells) +
                     f" | {row['direction']} better |")

    if analysis.get("reachable_decisions"):
        lines += ["", "### \\* Why the all-categories decision accuracy is not a fair comparison",
                  "", "Decisions each arm was able to emit at all:", ""]
        for arm in arms:
            lines.append(f"- **{arm}**: {', '.join(analysis['reachable_decisions'][arm])}")
        lines += ["",
                  "Only Cordon has a risk layer (`requires_approval`) and a reconciliation "
                  "loop (`reconcile`), so every `high_risk_action` and drifted "
                  "`configuration_drift` scenario is unwinnable for the other arms regardless "
                  "of planner quality. Cordon's rules and the dataset oracle are also two "
                  "encodings of the same `policies.json`, so its decision-match is largely a "
                  "correctness property of its own implementation. Use the "
                  "**enforcement-only** row for arm-vs-arm comparison, and the outcome-based "
                  "security metrics (which no arm is structurally favoured on) as the "
                  "primary evidence."]

    lines += ["", "## Decision accuracy by category", ""]
    lines += ["| category | " + " | ".join(arms) + " |", "|---" * (len(arms) + 1) + "|"]
    for category, per_arm in analysis["per_category"].items():
        cells = [f"{per_arm[a]*100:.1f}%" if per_arm[a] is not None else "n/a"
                 for a in arms]
        lines.append(f"| {category} | " + " | ".join(cells) + " |")

    lines += ["", "## Latency (mean ms per run)", ""]
    lines += ["| arm | plan | guardrails | exec | total |", "|---|---|---|---|---|"]
    for arm in arms:
        t = analysis["timing"][arm]
        lines.append(f"| {arm} | {t['mean_plan_ms']:.1f} | {t['mean_guardrail_ms']:.1f} "
                     f"| {t['mean_exec_ms']:.1f} | {t['mean_total_ms']:.1f} |")

    if analysis["flip_rate"]:
        lines += ["", "## Decision flip-rate across repetitions", ""]
        for arm, flip in analysis["flip_rate"].items():
            lines.append(f"- {arm}: {flip*100:.1f}% of scenarios changed decision "
                         f"between repetitions")

    if analysis["mcnemar"]:
        lines += ["", "## McNemar exact tests (baseline vs cordon, scenario level)", ""]
        for fact, m in analysis["mcnemar"].items():
            note = ""
            if m.get("degenerate"):
                n = m["baseline_only"] + m["cordon_only"]
                note = (f" — **one-sided discordance: p = 2^-{max(n-1,0)} is a deterministic "
                        f"function of the {n} discordant scenarios, not a measure of evidence "
                        f"strength. Report the effect, not this p.**")
            lines.append(f"- {fact}: baseline-only {m['baseline_only']}, cordon-only "
                         f"{m['cordon_only']}, of {m['n_scenarios']} scenarios; "
                         f"p = {m['p_value']:.2e}{note}")
        lines += ["",
                  "Tests are computed over scenarios (majority of repetitions), not runs. "
                  "The decision-accuracy test is restricted to the enforcement-only subset; "
                  "testing it over all categories would compare an arm that can emit "
                  "`requires_approval`/`reconcile` against arms that structurally cannot."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze experiment records")
    parser.add_argument("records", type=Path)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    records = load_records(args.records)
    report = render_markdown(analyze(records, dataset))
    summary_path = args.records.parent / "summary.md"
    summary_path.write_text(report)
    print(report)
    print(f"(written to {summary_path})")


if __name__ == "__main__":
    main()
