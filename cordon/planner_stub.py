"""Deterministic stub planner for offline development and harness tests.

Emits the action(s) literally requested by a scenario's intent, including
unsafe and injected ones — it models a maximally compliant planner so the
divergence between arms comes entirely from the control layers. The real
planner (an LLM behind the same signature) replaces this in live runs; its
actual behavior on unsafe requests is an empirical question the experiment
measures.

The stub reconstructs the requested action from the scenario's structured
fields rather than parsing free text, except where the intent itself is the
only source (invalid names/sizes in execution-failure scenarios).
"""

import re

from cordon.schema import Category, Plan, PlannedAction, Scenario

_ENVIRONMENTS = ("staging", "dev", "production")

_DELETE_VERBS = {
    "storage_bucket": ("delete_storage_bucket", "bucket_name"),
    "compute_instance": ("delete_compute_instance", "instance_id"),
    "security_group": ("delete_security_group", "security_group_id"),
}


def _act(name: str, **params) -> PlannedAction:
    return PlannedAction(action_name=name, parameters=params)


def _env_of(resource_id: str) -> str:
    prefix = resource_id.split("-")[0]
    return prefix if prefix in _ENVIRONMENTS else "staging"


def _encryption_type(intent: str) -> str:
    return "aws:kms" if "aws:kms" in intent else "AES256"


def _find(assertions, **conditions):
    for a in assertions:
        if all(a.get(k) == v for k, v in conditions.items()):
            return a
    return None


def stub_plan(scenario: Scenario) -> Plan:
    actions = _actions_for(scenario)
    return Plan(intent_summary=scenario.natural_language_intent[:80], actions=actions)


def _actions_for(s: Scenario) -> list[PlannedAction]:
    cat = s.category
    asserts = s.assertions
    intent = s.natural_language_intent

    if cat is Category.NORMAL_PROVISIONING:
        target = _find(asserts, exists=True)
        rid = target["resource_id"]
        if target["resource_type"] == "storage_bucket":
            plan = [_act("create_storage_bucket", bucket_name=rid,
                         environment=_env_of(rid), public_access=False)]
            if _find(asserts, attribute="encryption"):
                plan.append(_act("enable_bucket_encryption", bucket_name=rid,
                                 encryption_type=_encryption_type(intent)))
            return plan
        size = _find(asserts, attribute="size")["value"]
        return [_act("create_compute_instance", instance_name=rid, size=size,
                     environment=_env_of(rid))]

    if cat is Category.CONFIGURATION_CHANGE:
        a = next(a for a in asserts if "attribute" in a)
        rid = a["resource_id"]
        if a["attribute"] == "versioning":
            return [_act("enable_bucket_versioning", bucket_name=rid)]
        if a["attribute"] == "encryption":
            return [_act("enable_bucket_encryption", bucket_name=rid,
                         encryption_type=_encryption_type(intent))]
        rule = a["value"]
        return [_act("update_firewall_rule", security_group_id=rid,
                     port=rule["port"], protocol="tcp", cidr=rule["cidr"])]

    if cat is Category.POLICY_VIOLATION:
        rid = _find(asserts, exists=False)["resource_id"]
        refs = s.policy_refs
        if "POL-CMP-001" in refs:
            size = re.search(r"a (\w+) compute instance", intent).group(1)
            return [_act("create_compute_instance", instance_name=rid, size=size,
                         environment="production")]
        if "POL-STO-001" in refs:
            return [_act("create_storage_bucket", bucket_name=rid,
                         environment=_env_of(rid), public_access=True)]
        # POL-STO-002: create without ever enabling encryption
        return [_act("create_storage_bucket", bucket_name=rid,
                     environment=_env_of(rid), public_access=False)]

    if cat is Category.HIGH_RISK_ACTION:
        target = _find(asserts, exists=True)
        verb, key = _DELETE_VERBS[target["resource_type"]]
        return [_act(verb, **{key: target["resource_id"], "environment": "production"})]

    if cat is Category.NETWORK_MISCONFIGURATION:
        a = _find(asserts, attribute="has_rule")
        rule = a["value"]
        protocol = "all" if rule["port"] == 0 else "tcp"
        return [_act("update_firewall_rule", security_group_id=a["resource_id"],
                     port=rule["port"], protocol=protocol, cidr=rule["cidr"])]

    if cat is Category.CONFIGURATION_DRIFT:
        rid = s.drift_spec.resource_id if s.drift_spec else asserts[0]["resource_id"]
        # The planner only inspects; detecting and correcting drift is the
        # control plane's job (observation + reconciliation), not the planner's.
        return [_act("inspect_resource_state", resource_type="storage_bucket",
                     resource_id=rid)]

    if cat is Category.EXECUTION_FAILURE:
        if s.initial_state.resources:  # name conflict with a seeded resource
            rid = s.initial_state.resources[0].resource_id
            return [_act("create_storage_bucket", bucket_name=rid,
                         environment=_env_of(rid), public_access=False),
                    _act("enable_bucket_encryption", bucket_name=rid,
                         encryption_type="AES256")]
        if "size '" in intent:  # invalid instance size
            name = re.search(r"named (\S+)", intent).group(1)
            size = re.search(r"size '(.+?)'", intent).group(1)
            return [_act("create_compute_instance", instance_name=name, size=size,
                         environment="staging")]
        bad_name = re.search(r"named '(.+?)'", intent).group(1)  # invalid bucket name
        return [_act("create_storage_bucket", bucket_name=bad_name,
                     environment="staging", public_access=False),
                _act("enable_bucket_encryption", bucket_name=bad_name,
                     encryption_type="AES256")]

    if cat is Category.INPUT_ADVERSARIAL:
        seed = s.initial_state.resources
        metadata = seed[0].metadata if seed else None
        if metadata and "injected_port" in metadata:
            # Indirect injection: the stub models a planner that reads the
            # poisoned metadata note and obeys it.
            rid = seed[0].resource_id
            return [_act("inspect_resource_state", resource_type="security_group",
                         resource_id=rid),
                    _act("update_firewall_rule", security_group_id=rid,
                         port=metadata["injected_port"],
                         protocol=metadata["injected_protocol"],
                         cidr=metadata["injected_cidr"])]
        leak = next((a for a in asserts if "leak" in str(a.get("resource_id", ""))), None)
        if leak:
            return [_act("create_storage_bucket", bucket_name=leak["resource_id"],
                         environment="production", public_access=True)]
        target = seed[0]
        if target.resource_type == "security_group":
            return [_act("update_firewall_rule", security_group_id=target.resource_id,
                         port=22, protocol="tcp", cidr="0.0.0.0/0")]
        verb, key = _DELETE_VERBS[target.resource_type]
        return [_act(verb, **{key: target.resource_id, "environment": "production"})]

    raise ValueError(f"no stub plan for {s.scenario_id} ({cat})")
