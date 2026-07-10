"""The experiment arms: three pipelines from shared components.

- baseline           planner -> syntax validation -> execute
- prompt_guardrails  identical pipeline to baseline; the treatment is the
                     planner's prompt variant (chosen by the runner)
- cordon             planner -> adversarial -> capability -> policy -> risk
                     -> state check -> execute -> observe -> reconcile

Shared across arms (control variables): the plan schema, the execution
interface, the environment backend, and the per-run environment setup. An
empty plan (planner refusal) maps to decision "block" in every arm. Cordon
short-circuits at the first failing layer (fail-closed), matching the
sequential decision flow of the architecture; skipped layers are recorded
as not_evaluated.
"""

import time
from typing import Callable

from cordon.env.base import EnvironmentBackend
from cordon.execution import ExecutionInterface
from cordon.guardrails import (
    check_adversarial, check_capability, check_policies, check_risk, check_state, worst,
)
from cordon.reconcile import reconcile
from cordon.schema import (
    ActionSpec, Arm, Decision, ExecutionRecord, Plan, PlannerInfo, Policy,
    ReconcileInfo, Scenario,
)
from cordon.state_model import StateModel

NOT_EVALUATED = "not_evaluated"


def _timed(timing: dict[str, float], key: str, fn: Callable, *args):
    started = time.perf_counter()
    result = fn(*args)
    timing[key] = round((time.perf_counter() - started) * 1000, 3)
    return result


def _setup_environment(scenario: Scenario, env: EnvironmentBackend) -> None:
    env.load_initial_state(scenario.initial_state.resources)
    if scenario.drift_spec is not None:
        env.apply_drift(scenario.drift_spec)


def _finish(record_kwargs: dict, env: EnvironmentBackend, timing: dict[str, float]) -> ExecutionRecord:
    record_kwargs["final_state"] = _timed(timing, "observe", env.observe)
    timing["total"] = round(sum(timing.values()), 3)
    record_kwargs["timing_ms"] = timing
    return ExecutionRecord(**record_kwargs)


def run_baseline(
    scenario: Scenario,
    plan: Plan,
    planner_info: PlannerInfo,
    env: EnvironmentBackend,
    actions: list[ActionSpec],
    *,
    arm: Arm = Arm.BASELINE,
    rep: int = 0,
    run_id: str | None = None,
    dataset_hash: str = "",
    prompt_hash: str = "",
) -> ExecutionRecord:
    catalog = {a.action_name: a for a in actions}
    interface = ExecutionInterface(actions, env)
    timing: dict[str, float] = {"plan": round(planner_info.latency_ms, 3)}
    layers: dict[str, str] = {}

    _setup_environment(scenario, env)
    executed: list[str] = []
    error = None
    reached = False

    if plan.is_refusal:
        decision = Decision.BLOCK
        layers["blocked_by"] = "planner_refusal"
    else:
        syntax = _timed(timing, "syntax", check_capability, plan, catalog)
        layers["syntax"] = syntax.reason or "pass"
        if syntax.blocked:
            decision = Decision.FAIL_SAFE
            layers["blocked_by"] = "syntax"
        else:
            executed, error = _timed(timing, "exec", interface.execute_plan, plan)
            reached = True
            decision = Decision.FAIL_SAFE if error else Decision.ALLOW

    arm_label = getattr(arm, "value", arm)
    return _finish(dict(
        run_id=run_id or f"{scenario.scenario_id}-{arm_label}-r{rep}",
        scenario_id=scenario.scenario_id, arm=arm_label, rep=rep,
        dataset_hash=dataset_hash, prompt_hash=prompt_hash,
        planner=planner_info, plan=plan, layers=layers, decision=decision,
        executed=executed, error=error, reached_execution=reached,
    ), env, timing)


ALL_LAYERS = ("adversarial", "capability", "policy", "risk", "state")


def run_cordon(
    scenario: Scenario,
    plan: Plan,
    planner_info: PlannerInfo,
    env: EnvironmentBackend,
    actions: list[ActionSpec],
    policies: list[Policy],
    *,
    rep: int = 0,
    run_id: str | None = None,
    dataset_hash: str = "",
    prompt_hash: str = "",
    enabled_layers: set[str] | None = None,
    reconcile_enabled: bool = True,
    arm: Arm = Arm.CORDON,
) -> ExecutionRecord:
    # enabled_layers is for ablation studies; None means the full control plane.
    active = set(ALL_LAYERS) if enabled_layers is None else set(enabled_layers)
    catalog = {a.action_name: a for a in actions}
    interface = ExecutionInterface(actions, env)
    timing: dict[str, float] = {"plan": round(planner_info.latency_ms, 3)}
    layers: dict[str, str] = {key: NOT_EVALUATED for key in ALL_LAYERS}

    _setup_environment(scenario, env)
    model = StateModel.from_initial_state(scenario.initial_state.resources)
    reconcile_info = ReconcileInfo()
    executed: list[str] = []
    error = None
    reached = False
    decision: Decision | None = None

    def correction_guard(correction: Plan):
        # Corrections face the full layer set even when gates are ablated:
        # ablation removes a gate from the candidate-plan path only, never
        # from the correction path (a reduced control plane must not gain a
        # less-guarded route into the environment).
        return worst([
            check_capability(correction, catalog),
            check_policies(correction, policies),
            check_risk(correction, policies),
            check_state(correction, model),
        ])

    if plan.is_refusal:
        decision = Decision.BLOCK
        layers["blocked_by"] = "planner_refusal"
    else:
        candidate_gates = [
            ("adversarial", lambda: check_adversarial(
                scenario.natural_language_intent, scenario.initial_state.resources),
             Decision.BLOCK),
            ("capability", lambda: check_capability(plan, catalog), Decision.BLOCK),
            ("policy", lambda: check_policies(plan, policies), Decision.BLOCK),
            ("risk", lambda: check_risk(plan, policies), Decision.REQUIRES_APPROVAL),
            ("state", lambda: check_state(plan, model), Decision.BLOCK),
        ]
        gates = [g for g in candidate_gates if g[0] in active]
        for layer_name, gate, failing_decision in gates:
            verdict = _timed(timing, layer_name, gate)
            layers[layer_name] = verdict.reason or "pass"
            if not verdict.passed:
                decision = failing_decision
                layers["blocked_by"] = layer_name
                break

        if decision is None:
            executed, error = _timed(timing, "exec", interface.execute_plan, plan)
            reached = True
            for action in plan.actions[:len(executed)]:
                model.apply_action(action)
            if reconcile_enabled:
                reconcile_info = _timed(timing, "reconcile", reconcile,
                                        model, env, interface, correction_guard)
            if error:
                decision = Decision.FAIL_SAFE
            elif reconcile_info.drift_detected:
                decision = Decision.RECONCILE
                executed = executed + list(reconcile_info.correction_executed)
            else:
                decision = Decision.ALLOW

    arm_label = getattr(arm, "value", arm)
    return _finish(dict(
        run_id=run_id or f"{scenario.scenario_id}-{arm_label}-r{rep}",
        scenario_id=scenario.scenario_id, arm=arm_label, rep=rep,
        dataset_hash=dataset_hash, prompt_hash=prompt_hash,
        planner=planner_info, plan=plan, layers=layers, decision=decision,
        executed=executed, error=error, reached_execution=reached,
        reconcile=reconcile_info,
    ), env, timing)
