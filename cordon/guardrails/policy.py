"""Policy enforcement (layer 2): block-effect policies from policies.json.

Each policy id in the central specification maps to one rule implementation
here; loading a policy with no implementation fails loudly rather than
silently not enforcing it. Approval-effect policies are the risk layer's
job and are skipped here.

Rules evaluate the whole (normalized) plan, which is what makes the
cross-action encryption rule possible: creating a bucket is compliant only
if the same plan also encrypts it.
"""

from typing import Callable

from cordon.execution import normalize_action
from cordon.guardrails.base import BLOCK, LayerVerdict
from cordon.schema import Plan, PlannedAction, Policy

UNRESTRICTED_CIDR = "0.0.0.0/0"


def _rule_no_open_port(port: int) -> Callable[[list[PlannedAction]], bool]:
    def violated(actions: list[PlannedAction]) -> bool:
        return any(
            a.action_name == "update_firewall_rule"
            and a.parameters.get("cidr") == UNRESTRICTED_CIDR
            and a.parameters.get("port") == port
            for a in actions
        )
    return violated


def _rule_no_open_all_ports(actions: list[PlannedAction]) -> bool:
    return any(
        a.action_name == "update_firewall_rule"
        and a.parameters.get("cidr") == UNRESTRICTED_CIDR
        and (a.parameters.get("port") == 0 or str(a.parameters.get("protocol")).lower() == "all")
        for a in actions
    )


def _rule_no_public_bucket(actions: list[PlannedAction]) -> bool:
    return any(
        a.action_name in ("create_storage_bucket", "set_bucket_public_access")
        and a.parameters.get("public_access") is True
        for a in actions
    )


def _rule_bucket_must_encrypt(actions: list[PlannedAction]) -> bool:
    created = {a.parameters.get("bucket_name") for a in actions
               if a.action_name == "create_storage_bucket"}
    encrypted = {a.parameters.get("bucket_name") for a in actions
                 if a.action_name == "enable_bucket_encryption"}
    return bool(created - encrypted)


def _rule_production_size_ceiling(actions: list[PlannedAction]) -> bool:
    oversized = {"xlarge", "2xlarge", "4xlarge"}
    return any(
        a.action_name == "create_compute_instance"
        and a.parameters.get("environment") == "production"
        and a.parameters.get("size") in oversized
        for a in actions
    )


RULE_IMPLEMENTATIONS: dict[str, Callable[[list[PlannedAction]], bool]] = {
    "POL-NET-001": _rule_no_open_port(22),
    "POL-NET-002": _rule_no_open_port(3389),
    "POL-NET-003": _rule_no_open_all_ports,
    "POL-STO-001": _rule_no_public_bucket,
    "POL-STO-002": _rule_bucket_must_encrypt,
    "POL-CMP-001": _rule_production_size_ceiling,
}


def check_policies(plan: Plan, policies: list[Policy]) -> LayerVerdict:
    actions = [normalize_action(a) for a in plan.actions]
    violated: list[str] = []
    for policy in policies:
        if policy.effect != "block":
            continue  # approval-effect policies belong to the risk layer
        rule = RULE_IMPLEMENTATIONS.get(policy.policy_id)
        if rule is None:
            raise ValueError(f"policy {policy.policy_id} has no rule implementation")
        if rule(actions):
            violated.append(policy.policy_id)
    if violated:
        return LayerVerdict(outcome=BLOCK, reason="policy_violation:" + ",".join(violated))
    return LayerVerdict()
