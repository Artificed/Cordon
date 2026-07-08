"""Core domain models shared by every component.

The vocabulary here mirrors the dataset schema (v2): scenarios are test
cases with a machine-checkable oracle; plans are ordered lists of
predefined actions; execution records are the per-run bridge between the
pipeline and the metrics.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Decision(StrEnum):
    """Plan-level disposition. Severity order: block > requires_approval > allow."""

    ALLOW = "allow"
    BLOCK = "block"
    REQUIRES_APPROVAL = "requires_approval"
    RECONCILE = "reconcile"
    FAIL_SAFE = "fail_safe"


class SafetyLabel(StrEnum):
    """What, if anything, is wrong with the request. Security metrics average over this."""

    SAFE = "safe"
    POLICY_VIOLATION = "policy_violation"
    HARMFUL = "harmful"
    ADVERSARIAL = "adversarial"
    INVALID_REQUEST = "invalid_request"


class Category(StrEnum):
    """The 8 risk areas; every metric is reported per category."""

    NORMAL_PROVISIONING = "normal_provisioning"
    CONFIGURATION_CHANGE = "configuration_change"
    POLICY_VIOLATION = "policy_violation"
    HIGH_RISK_ACTION = "high_risk_action"
    NETWORK_MISCONFIGURATION = "network_misconfiguration"
    CONFIGURATION_DRIFT = "configuration_drift"
    EXECUTION_FAILURE = "execution_failure"
    INPUT_ADVERSARIAL = "input_adversarial"


class Environment(StrEnum):
    STAGING = "staging"
    DEV = "dev"
    PRODUCTION = "production"


class Arm(StrEnum):
    """Experiment configurations under comparison."""

    BASELINE = "baseline"
    PROMPT_GUARDRAILS = "prompt_guardrails"
    CORDON = "cordon"


# --------------------------------------------------------------------------
# Dataset models
# --------------------------------------------------------------------------

class Resource(BaseModel):
    resource_type: str
    resource_id: str
    environment: Environment
    attributes: dict[str, Any] = Field(default_factory=dict)
    # metadata may contain harness-internal fields (injected_*) that must
    # never reach the planner; context building filters them.
    metadata: dict[str, Any] | None = None


class InitialState(BaseModel):
    resources: list[Resource] = Field(default_factory=list)


class DriftSpec(BaseModel):
    """Divergence applied to the environment only (never the state model) at setup."""

    resource_id: str
    attribute: str
    desired_value: Any
    actual_value: Any


class Scenario(BaseModel):
    scenario_id: str
    category: Category
    safety_label: SafetyLabel
    natural_language_intent: str
    initial_state: InitialState
    drift_spec: DriftSpec | None = None
    policy_refs: list[str] = Field(default_factory=list)
    expected_decision: Decision
    # Assertions are heterogeneous ({exists} / {attribute, value} / {invariant});
    # they are consumed only by the scorer, so they stay as plain dicts.
    expected_final_state: dict[str, list[dict[str, Any]]]
    failure_reason: str | None = None
    injection_type: str | None = None

    @property
    def assertions(self) -> list[dict[str, Any]]:
        return self.expected_final_state["assertions"]


class Policy(BaseModel):
    policy_id: str
    title: str
    rule: str
    resource_type: str
    effect: str  # "block" | "requires_approval"
    source: str


class ActionSpec(BaseModel):
    """One entry of the predefined action catalog (the capability list)."""

    action_name: str
    target_resource_type: str
    description: str
    parameters: list[str]
    mutating: bool


class Dataset(BaseModel):
    meta: dict[str, Any]
    policies: list[Policy]
    actions: list[ActionSpec]
    scenarios: list[Scenario]

    def action_names(self) -> set[str]:
        return {a.action_name for a in self.actions}

    def mutating_action_names(self) -> set[str]:
        return {a.action_name for a in self.actions if a.mutating}


# --------------------------------------------------------------------------
# Plan models (the LLM planner's output shape)
# --------------------------------------------------------------------------

class PlannedAction(BaseModel):
    action_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    """An ordered list of 0–5 actions. An empty list is a planner refusal."""

    intent_summary: str = ""
    actions: list[PlannedAction] = Field(default_factory=list, max_length=5)

    @property
    def is_refusal(self) -> bool:
        return len(self.actions) == 0


# --------------------------------------------------------------------------
# Execution record (one per run; the raw material for every metric)
# --------------------------------------------------------------------------

class PlannerInfo(BaseModel):
    model: str = "stub"
    response_id: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    refused: bool = False
    shared_with_baseline: bool = False


class ReconcileInfo(BaseModel):
    ran: bool = False
    drift_detected: bool | None = None
    correction_plan: Plan | None = None
    correction_executed: list[str] = Field(default_factory=list)
    correction_error: str | None = None


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    scenario_id: str
    arm: Arm
    rep: int
    dataset_hash: str = ""
    prompt_hash: str = ""
    planner: PlannerInfo = Field(default_factory=PlannerInfo)
    plan: Plan = Field(default_factory=Plan)
    layers: dict[str, Any] = Field(default_factory=dict)
    decision: Decision
    executed: list[str] = Field(default_factory=list)
    error: str | None = None
    reached_execution: bool = False
    reconcile: ReconcileInfo = Field(default_factory=ReconcileInfo)
    final_state: dict[str, Any] = Field(default_factory=dict)
    timing_ms: dict[str, float] = Field(default_factory=dict)
