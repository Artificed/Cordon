"""Capability control (layer 1).

Is every planned action within what the system can do at all: a catalog
action, with its required parameters present, in a known environment.
Semantic parameter *values* (naming rules, size enums) are the execution
interface's contract and fail safe there — capability answers "can the
system do this kind of thing", not "will this succeed".

The baseline arm's "basic syntax validation" is this same check, which
keeps that stage a controlled variable across arms.
"""

from cordon.execution import normalize_action
from cordon.guardrails.base import BLOCK, LayerVerdict, worst
from cordon.schema import ActionSpec, Environment, Plan


def check_capability(plan: Plan, catalog: dict[str, ActionSpec]) -> LayerVerdict:
    verdicts = []
    for action in plan.actions:
        spec = catalog.get(action.action_name)
        if spec is None:
            verdicts.append(LayerVerdict(
                outcome=BLOCK, reason=f"out_of_capability:{action.action_name}"))
            continue
        normalized = normalize_action(action)
        missing = [p for p in spec.parameters if normalized.parameters.get(p) is None]
        if missing:
            verdicts.append(LayerVerdict(
                outcome=BLOCK,
                reason=f"missing_parameter:{action.action_name}:{','.join(missing)}"))
            continue
        environment = normalized.parameters.get("environment")
        if environment is not None and environment not in set(Environment):
            verdicts.append(LayerVerdict(
                outcome=BLOCK, reason=f"unknown_environment:{environment}"))
            continue
        verdicts.append(LayerVerdict())
    return worst(verdicts)
