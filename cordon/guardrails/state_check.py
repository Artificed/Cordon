"""State model check (layer 4): plan preconditions against known state.

Plans are ordered, so preconditions are checked against a *projection*: each
action is validated against the state the preceding actions would produce
(a plan may configure the bucket it just created). The real model is never
mutated here — beliefs only change after actual execution.
"""

from cordon.guardrails.base import BLOCK, LayerVerdict
from cordon.schema import Plan
from cordon.state_model import StateModel


def check_state(plan: Plan, model: StateModel) -> LayerVerdict:
    projection = model.clone()
    for action in plan.actions:
        reason = projection.check_action(action)
        if reason:
            return LayerVerdict(outcome=BLOCK, reason=reason)
        projection.apply_action(action)
    return LayerVerdict()
