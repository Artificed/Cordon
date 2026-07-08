"""Observation and reconciliation: detect drift, correct it, stay guardrailed.

Detection is the state model's desired-vs-observed diff. Corrections are
ordinary catalog actions assembled from a fixed attribute map and re-enter
the validation pipeline like any plan — a correction is still an action.
One correction cycle per run: after correcting, the environment is
re-observed into the state model but not re-diffed (no correction loops).
"""

from typing import Callable

from cordon.env.base import EnvironmentBackend
from cordon.execution import ExecutionInterface
from cordon.guardrails.base import LayerVerdict
from cordon.schema import Plan, PlannedAction, ReconcileInfo
from cordon.state_model import DriftItem, StateModel


def correction_for(drift: DriftItem) -> PlannedAction | None:
    """The catalog action that restores one drifted attribute, if any."""
    if drift.resource_type != "storage_bucket":
        return None
    bucket = drift.resource_id
    if drift.attribute == "encryption" and drift.desired_value == "enabled":
        return PlannedAction(action_name="enable_bucket_encryption",
                             parameters={"bucket_name": bucket,
                                         "encryption_type": "AES256"})
    if drift.attribute == "versioning" and drift.desired_value is True:
        return PlannedAction(action_name="enable_bucket_versioning",
                             parameters={"bucket_name": bucket})
    if drift.attribute == "public_access":
        return PlannedAction(action_name="set_bucket_public_access",
                             parameters={"bucket_name": bucket,
                                         "public_access": bool(drift.desired_value)})
    return None


def reconcile(
    model: StateModel,
    env: EnvironmentBackend,
    interface: ExecutionInterface,
    guard: Callable[[Plan], LayerVerdict],
) -> ReconcileInfo:
    observed = env.observe()
    drifts = model.diff_desired(observed)
    if not drifts:
        model.refresh(observed)
        return ReconcileInfo(ran=True, drift_detected=False)

    actions = []
    for drift in drifts:
        action = correction_for(drift)
        if action is None:
            return ReconcileInfo(
                ran=True, drift_detected=True,
                correction_error=f"uncorrectable:{drift.resource_id}.{drift.attribute}")
        actions.append(action)
    correction = Plan(
        intent_summary=f"reconcile {len(drifts)} drifted attribute(s)",
        actions=actions)

    verdict = guard(correction)
    if not verdict.passed:
        return ReconcileInfo(
            ran=True, drift_detected=True, correction_plan=correction,
            correction_error=f"correction_{verdict.outcome}:{verdict.reason}")

    executed, error = interface.execute_plan(correction)
    for action in correction.actions[:len(executed)]:
        model.apply_action(action)
    model.refresh(env.observe())
    return ReconcileInfo(ran=True, drift_detected=True, correction_plan=correction,
                         correction_executed=executed, correction_error=error)
