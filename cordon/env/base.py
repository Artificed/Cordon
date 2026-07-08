"""Environment backend interface.

A backend holds the *actual* infrastructure state for one run: the in-memory
backend for development and unit tests, the LocalStack backend for the real
experiment. Backends only mutate and report state; parameter validation and
existence pre-checks belong to the execution interface, and desired-state
bookkeeping belongs to the state model.
"""

from abc import ABC, abstractmethod
from typing import Any

from cordon.schema import DriftSpec, PlannedAction, Resource


class EnvironmentBackend(ABC):
    @abstractmethod
    def load_initial_state(self, resources: list[Resource]) -> None:
        """Create the scenario's starting resources in a clean environment."""

    @abstractmethod
    def apply_drift(self, drift: DriftSpec) -> None:
        """Diverge the environment from the recorded desired state.

        Applied to the environment only — the state model keeps believing
        the desired value. This is how drift scenarios are staged.
        """

    @abstractmethod
    def exists(self, resource_id: str) -> bool: ...

    @abstractmethod
    def get(self, resource_id: str) -> dict[str, Any] | None:
        """Snapshot of one resource (a copy; mutating it changes nothing)."""

    @abstractmethod
    def apply(self, action: PlannedAction) -> str | None:
        """Mutate state for one validated action. Returns an error code or None."""

    @abstractmethod
    def observe(self) -> dict[str, dict[str, Any]]:
        """Full snapshot keyed by resource_id (copies)."""

    @abstractmethod
    def cleanup(self) -> None:
        """Remove every resource, returning the environment to empty."""
