"""State model check (layer 4): plan preconditions against known state."""

from cordon.guardrails.base import BLOCK, LayerVerdict, worst
from cordon.schema import Plan
from cordon.state_model import StateModel


def check_state(plan: Plan, model: StateModel) -> LayerVerdict:
    verdicts = []
    for action in plan.actions:
        reason = model.check_action(action)
        verdicts.append(LayerVerdict(outcome=BLOCK, reason=reason) if reason
                        else LayerVerdict())
    return worst(verdicts)
