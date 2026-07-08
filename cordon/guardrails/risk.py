"""Capability-and-risk tiering (layer 3): approval-effect policies.

Actions that are legitimate but high-impact escalate to a human instead of
executing — in this catalog, deleting anything in production (POL-DEL-001).
In the experiments escalation is terminal: the action does not execute and
the run is scored against the oracle's expected decision.
"""

from cordon.execution import normalize_action
from cordon.guardrails.base import REQUIRES_APPROVAL, LayerVerdict
from cordon.schema import Plan, Policy


def _production_delete(actions) -> bool:
    return any(
        a.action_name.startswith("delete_")
        and a.parameters.get("environment") == "production"
        for a in actions
    )


APPROVAL_RULES = {
    "POL-DEL-001": _production_delete,
}


def check_risk(plan: Plan, policies: list[Policy]) -> LayerVerdict:
    actions = [normalize_action(a) for a in plan.actions]
    for policy in policies:
        if policy.effect != "requires_approval":
            continue
        rule = APPROVAL_RULES.get(policy.policy_id)
        if rule is None:
            raise ValueError(f"policy {policy.policy_id} has no risk rule implementation")
        if rule(actions):
            return LayerVerdict(outcome=REQUIRES_APPROVAL,
                                reason=f"high_risk:{policy.policy_id}")
    return LayerVerdict()
