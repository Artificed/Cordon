"""The state model: the control plane's believed and desired view of resources.

Per managed resource it records the believed attributes (updated after every
successful execution, refreshed from observations) and, when the scenario
provides one, an explicit desired configuration. The model backs two layers:

- the state check — per-verb preconditions a plan must satisfy against
  *known* state (create: target must be new; modify/delete/inspect: target
  must exist and the plan's environment must match);
- reconciliation — diffing explicit desired configurations against observed
  reality to detect configuration drift.
"""

from typing import Any

from pydantic import BaseModel, Field

from cordon.execution import TARGET_PARAMS, normalize_action
from cordon.schema import PlannedAction, Resource

# Attribute updates implied by each mutating action (applied to beliefs).
_ATTRIBUTE_EFFECTS: dict[str, tuple[str, Any]] = {
    "enable_bucket_encryption": ("encryption", "enabled"),
    "enable_bucket_versioning": ("versioning", True),
}


class ResourceRecord(BaseModel):
    resource_id: str
    resource_type: str
    environment: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    desired_state: dict[str, Any] | None = None


class DriftItem(BaseModel):
    resource_id: str
    resource_type: str
    attribute: str
    desired_value: Any
    observed_value: Any


class StateModel:
    def __init__(self) -> None:
        self._records: dict[str, ResourceRecord] = {}

    @classmethod
    def from_initial_state(cls, resources: list[Resource]) -> "StateModel":
        model = cls()
        for r in resources:
            attributes = dict(r.attributes)
            desired = attributes.pop("desired_state", None)
            model._records[r.resource_id] = ResourceRecord(
                resource_id=r.resource_id,
                resource_type=r.resource_type,
                environment=str(r.environment),
                attributes=attributes,
                desired_state=desired,
            )
        return model

    def exists(self, resource_id: str) -> bool:
        return resource_id in self._records

    def get(self, resource_id: str) -> ResourceRecord | None:
        return self._records.get(resource_id)

    # ------------------------------------------------------------------
    # The state check (per-verb preconditions)
    # ------------------------------------------------------------------

    def check_action(self, action: PlannedAction) -> str | None:
        """Precondition check against known state. Returns a reason or None."""
        if action.action_name not in TARGET_PARAMS:
            return None  # capability's problem, not the state model's
        action = normalize_action(action)
        target_param, verb = TARGET_PARAMS[action.action_name]
        target = action.parameters.get(target_param)
        if target is None:
            return None

        if verb == "create":
            if self.exists(target):
                return f"state_mismatch:already_exists:{target}"
            return None

        record = self.get(target)
        if record is None:
            return f"state_mismatch:unknown_resource:{target}"
        planned_environment = action.parameters.get("environment")
        if planned_environment is not None and planned_environment != record.environment:
            return (f"state_mismatch:environment:{target}:"
                    f"plan={planned_environment},known={record.environment}")
        return None

    # ------------------------------------------------------------------
    # Belief updates
    # ------------------------------------------------------------------

    def apply_action(self, action: PlannedAction) -> None:
        """Update beliefs after the execution interface ran an action."""
        action = normalize_action(action)
        name, p = action.action_name, action.parameters

        if name == "create_storage_bucket":
            self._records[p["bucket_name"]] = ResourceRecord(
                resource_id=p["bucket_name"], resource_type="storage_bucket",
                environment=p["environment"],
                attributes={"encryption": "disabled",
                            "public_access": bool(p.get("public_access", False))},
            )
        elif name == "create_compute_instance":
            self._records[p["instance_name"]] = ResourceRecord(
                resource_id=p["instance_name"], resource_type="compute_instance",
                environment=p["environment"],
                attributes={"size": p["size"], "status": "running"},
            )
        elif name in _ATTRIBUTE_EFFECTS:
            attribute, value = _ATTRIBUTE_EFFECTS[name]
            record = self._records.get(p["bucket_name"])
            if record:
                record.attributes[attribute] = value
        elif name == "set_bucket_public_access":
            record = self._records.get(p["bucket_name"])
            if record:
                record.attributes["public_access"] = bool(p["public_access"])
        elif name == "update_firewall_rule":
            record = self._records.get(p["security_group_id"])
            if record:
                record.attributes.setdefault("ingress_rules", []).append(
                    {"port": p["port"], "cidr": p["cidr"]})
        elif name.startswith("delete_"):
            target_param, _ = TARGET_PARAMS[name]
            self._records.pop(p.get(target_param), None)

    def refresh(self, observed: dict[str, dict[str, Any]]) -> None:
        """Replace believed attributes with observed reality (desired stays)."""
        for rid, seen in observed.items():
            record = self._records.get(rid)
            if record is None:
                self._records[rid] = ResourceRecord(
                    resource_id=rid, resource_type=seen["resource_type"],
                    environment=seen["environment"], attributes=dict(seen["attributes"]),
                )
            else:
                record.attributes = dict(seen["attributes"])
        for rid in list(self._records):
            if rid not in observed:
                del self._records[rid]

    # ------------------------------------------------------------------
    # Drift detection
    # ------------------------------------------------------------------

    def diff_desired(self, observed: dict[str, dict[str, Any]]) -> list[DriftItem]:
        """Explicit desired configurations vs observed reality."""
        drifts: list[DriftItem] = []
        for record in self._records.values():
            if not record.desired_state:
                continue
            seen = observed.get(record.resource_id)
            for attribute, desired in record.desired_state.items():
                observed_value = None if seen is None else seen["attributes"].get(attribute)
                if observed_value != desired:
                    drifts.append(DriftItem(
                        resource_id=record.resource_id,
                        resource_type=record.resource_type,
                        attribute=attribute,
                        desired_value=desired,
                        observed_value=observed_value,
                    ))
        return drifts
