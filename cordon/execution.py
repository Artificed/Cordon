"""The execution interface: the only door to the environment.

Deterministic and shared by every arm. It maps planned actions onto the
predefined catalog, validates parameters, pre-checks existence, and only
then dispatches to the environment backend. Validation lives here — not in
the backends — so semantics are identical for the in-memory and LocalStack
environments, and identical for every arm (a control variable of the
experiment).

Error codes align with the dataset's failure vocabulary:
``unknown_action`` / ``missing_parameter`` / ``invalid_parameter`` /
``invalid_resource_name`` / ``name_conflict`` / ``not_found``.
"""

import re

from cordon.env.base import EnvironmentBackend
from cordon.schema import ActionSpec, Environment, Plan, PlannedAction

VALID_SIZES = {"small", "medium", "large", "xlarge", "2xlarge", "4xlarge"}
VALID_ENCRYPTION_TYPES = {"AES256", "aws:kms"}
VALID_PROTOCOLS = {"tcp", "udp", "icmp", "all"}

# Subset of the S3 naming rules, sufficient for this namespace: 3-63 chars,
# lowercase alphanumerics and hyphens, must start and end alphanumeric.
BUCKET_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")

# Parameters that may be omitted by the planner, with the value then assumed.
PARAM_DEFAULTS: dict[str, dict[str, object]] = {
    "create_storage_bucket": {"public_access": False},
    "update_firewall_rule": {"protocol": "tcp"},
}

# How each action addresses its target: (parameter holding the id, verb).
# create -> target must not exist; modify/delete/inspect -> target must exist.
TARGET_PARAMS: dict[str, tuple[str, str]] = {
    "create_storage_bucket": ("bucket_name", "create"),
    "enable_bucket_encryption": ("bucket_name", "modify"),
    "enable_bucket_versioning": ("bucket_name", "modify"),
    "set_bucket_public_access": ("bucket_name", "modify"),
    "delete_storage_bucket": ("bucket_name", "delete"),
    "create_compute_instance": ("instance_name", "create"),
    "delete_compute_instance": ("instance_id", "delete"),
    "delete_security_group": ("security_group_id", "delete"),
    "update_firewall_rule": ("security_group_id", "modify"),
    "inspect_resource_state": ("resource_id", "inspect"),
}


def normalize_action(action: PlannedAction) -> PlannedAction:
    """Fill defaulted parameters so downstream code sees complete inputs."""
    defaults = PARAM_DEFAULTS.get(action.action_name, {})
    params = dict(action.parameters)
    for key, value in defaults.items():
        if params.get(key) is None:
            params[key] = value
    return PlannedAction(action_name=action.action_name, parameters=params)


def validate_action(action: PlannedAction, catalog: dict[str, ActionSpec]) -> str | None:
    """Static validation against the catalog. No environment access."""
    spec = catalog.get(action.action_name)
    if spec is None:
        return "unknown_action"

    action = normalize_action(action)
    p = action.parameters
    for required in spec.parameters:
        if p.get(required) is None:
            return "missing_parameter"

    if "environment" in spec.parameters:
        if p["environment"] not in set(Environment):
            return "invalid_parameter"

    if action.action_name in ("create_storage_bucket",):
        if not isinstance(p["bucket_name"], str) or not BUCKET_NAME_RE.fullmatch(p["bucket_name"]):
            return "invalid_resource_name"

    if action.action_name == "create_compute_instance":
        if p["size"] not in VALID_SIZES:
            return "invalid_parameter"

    if action.action_name == "enable_bucket_encryption":
        if p["encryption_type"] not in VALID_ENCRYPTION_TYPES:
            return "invalid_parameter"

    if action.action_name == "set_bucket_public_access":
        if not isinstance(p["public_access"], bool):
            return "invalid_parameter"

    if action.action_name == "update_firewall_rule":
        port = p["port"]
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            return "invalid_parameter"
        if str(p["protocol"]).lower() not in VALID_PROTOCOLS:
            return "invalid_parameter"

    return None


class ExecutionInterface:
    def __init__(self, actions: list[ActionSpec], env: EnvironmentBackend) -> None:
        self.catalog = {a.action_name: a for a in actions}
        self.env = env

    def execute_action(self, action: PlannedAction) -> str | None:
        """Validate, pre-check existence, then apply. Returns an error code or None."""
        error = validate_action(action, self.catalog)
        if error:
            return error
        action = normalize_action(action)

        target_param, verb = TARGET_PARAMS[action.action_name]
        target = action.parameters[target_param]
        if verb == "create" and self.env.exists(target):
            return "name_conflict"
        if verb in ("modify", "delete", "inspect") and not self.env.exists(target):
            return "not_found"

        return self.env.apply(action)

    def execute_plan(self, plan: Plan) -> tuple[list[str], str | None]:
        """Execute actions in order, halting on the first error (fail-safe:
        halt-and-report, no rollback). Returns (executed action names, error)."""
        executed: list[str] = []
        for action in plan.actions:
            error = self.execute_action(action)
            if error:
                return executed, error
            executed.append(action.action_name)
        return executed, None
