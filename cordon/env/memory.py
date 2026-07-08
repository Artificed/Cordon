"""In-memory environment backend.

Semantics mirror what the LocalStack backend does with real API calls:
buckets are created unencrypted and private-by-default, firewall rules
append to a security group's ingress list, deletes remove the resource.
Used by unit tests and by offline development runs.
"""

from copy import deepcopy
from typing import Any

from cordon.env.base import EnvironmentBackend
from cordon.schema import DriftSpec, PlannedAction, Resource


class InMemoryEnvironment(EnvironmentBackend):
    def __init__(self) -> None:
        self._resources: dict[str, dict[str, Any]] = {}

    def load_initial_state(self, resources: list[Resource]) -> None:
        self.cleanup()
        for r in resources:
            entry = r.model_dump(mode="json", exclude_none=True)
            # desired_state is state-model bookkeeping; a real environment
            # cannot hold it, so this backend must not either.
            entry["attributes"].pop("desired_state", None)
            self._resources[r.resource_id] = entry

    def apply_drift(self, drift: DriftSpec) -> None:
        resource = self._resources[drift.resource_id]
        resource["attributes"][drift.attribute] = drift.actual_value

    def exists(self, resource_id: str) -> bool:
        return resource_id in self._resources

    def get(self, resource_id: str) -> dict[str, Any] | None:
        r = self._resources.get(resource_id)
        return deepcopy(r) if r is not None else None

    def observe(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._resources)

    def cleanup(self) -> None:
        self._resources.clear()

    def apply(self, action: PlannedAction) -> str | None:
        name, p = action.action_name, action.parameters

        if name == "create_storage_bucket":
            bucket = p["bucket_name"]
            if bucket in self._resources:
                return "name_conflict"
            self._resources[bucket] = {
                "resource_type": "storage_bucket",
                "resource_id": bucket,
                "environment": p["environment"],
                "attributes": {
                    "encryption": "disabled",
                    "public_access": bool(p.get("public_access", False)),
                },
            }

        elif name == "enable_bucket_encryption":
            return self._set_attribute(p["bucket_name"], "encryption", "enabled")

        elif name == "enable_bucket_versioning":
            return self._set_attribute(p["bucket_name"], "versioning", True)

        elif name == "set_bucket_public_access":
            return self._set_attribute(p["bucket_name"], "public_access", bool(p["public_access"]))

        elif name == "create_compute_instance":
            instance = p["instance_name"]
            if instance in self._resources:
                return "name_conflict"
            self._resources[instance] = {
                "resource_type": "compute_instance",
                "resource_id": instance,
                "environment": p["environment"],
                "attributes": {"size": p["size"], "status": "running"},
            }

        elif name in ("delete_storage_bucket", "delete_compute_instance", "delete_security_group"):
            rid = p.get("bucket_name") or p.get("instance_id") or p.get("security_group_id")
            if rid not in self._resources:
                return "not_found"
            del self._resources[rid]

        elif name == "update_firewall_rule":
            sg = self._resources.get(p["security_group_id"])
            if sg is None:
                return "not_found"
            # Canonical observed rule shape across backends: {port, cidr}.
            sg["attributes"].setdefault("ingress_rules", []).append(
                {"port": p["port"], "cidr": p["cidr"]}
            )

        elif name == "inspect_resource_state":
            if p["resource_id"] not in self._resources:
                return "not_found"

        else:
            return "unknown_action"

        return None

    def _set_attribute(self, resource_id: str, attribute: str, value: Any) -> str | None:
        resource = self._resources.get(resource_id)
        if resource is None:
            return "not_found"
        resource["attributes"][attribute] = value
        return None
