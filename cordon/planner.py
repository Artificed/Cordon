"""Planners: natural-language intent -> structured action plan.

Two implementations behind one signature:

- ``StubPlanner`` — the deterministic offline stand-in (development, tests).
- ``OpenAIPlanner`` — the real planner: OpenAI Responses API with a strict
  JSON-schema output format, so the model can only emit catalog actions in
  the shared plan shape.

Experimental controls enforced here:

- The planner sees ONLY the intent and a read-only resource inventory —
  never the scenario's oracle fields (``plan_for`` extracts exactly those
  two and nothing else).
- Harness-internal ``metadata.injected_*`` fields are stripped from the
  inventory; ``metadata.note`` is kept (it is the indirect-injection channel).
- The system prompt is rendered once per variant and hashed; the hash goes
  into every run manifest. The ``core`` variant (baseline and cordon arms)
  contains no policy or safety language; the ``promptguard`` variant appends
  the policy list and refusal instructions — that difference is the
  prompt-guardrails treatment.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Protocol

from openai import OpenAI
from pydantic import ValidationError

from cordon.schema import ActionSpec, Plan, PlannedAction, PlannerInfo, Policy, Resource, Scenario
from cordon.planner_stub import stub_plan

PROMPT_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSION = "v1"

# Union of every catalog parameter; the plan schema is flat-nullable because
# strict structured output requires all properties to be required, so unused
# parameters are null rather than absent.
PARAM_FIELDS: dict[str, dict[str, Any]] = {
    "bucket_name": {"type": ["string", "null"]},
    "instance_name": {"type": ["string", "null"]},
    "instance_id": {"type": ["string", "null"]},
    "security_group_id": {"type": ["string", "null"]},
    "resource_type": {"type": ["string", "null"]},
    "resource_id": {"type": ["string", "null"]},
    "environment": {"type": ["string", "null"]},
    "size": {"type": ["string", "null"]},
    "encryption_type": {"type": ["string", "null"]},
    "public_access": {"type": ["boolean", "null"]},
    "port": {"type": ["integer", "null"]},
    "protocol": {"type": ["string", "null"]},
    "cidr": {"type": ["string", "null"]},
}


class PlannerError(RuntimeError):
    """The planner could not produce a parseable plan."""


# --------------------------------------------------------------------------
# Rendering: schema, prompts, inventory
# --------------------------------------------------------------------------

def plan_json_schema(action_names: set[str]) -> dict[str, Any]:
    """Strict-mode JSON schema for the plan. Plan length (<=5) is enforced at
    parse time because strict structured output does not support maxItems."""
    action_properties: dict[str, Any] = {
        "action_name": {"type": "string", "enum": sorted(action_names)},
        **PARAM_FIELDS,
    }
    return {
        "type": "object",
        "properties": {
            "intent_summary": {"type": "string"},
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": action_properties,
                    "required": list(action_properties),
                    "additionalProperties": False,
                },
            },
        },
        "required": ["intent_summary", "actions"],
        "additionalProperties": False,
    }


def render_action_catalog(actions: list[ActionSpec]) -> str:
    lines = []
    for a in actions:
        params = ", ".join(a.parameters)
        lines.append(f"- {a.action_name} (target: {a.target_resource_type}) — "
                     f"{a.description} Parameters: {params}.")
    return "\n".join(lines)


def render_policies(policies: list[Policy]) -> str:
    return "\n".join(
        f"- {p.policy_id} — {p.rule} (effect: {p.effect}; source: {p.source})"
        for p in policies
    )


def render_inventory(resources: list[Resource]) -> str:
    """Read-only inventory snapshot shown to the planner.

    ``metadata.injected_*`` fields are harness-internal oracle data and are
    stripped; ``metadata.note`` stays — a planner reading a poisoned note is
    exactly what the indirect-injection scenarios test.
    """
    if not resources:
        return "(no resources exist yet)"
    entries = []
    for r in resources:
        entry = r.model_dump(mode="json", exclude_none=True)
        metadata = entry.get("metadata")
        if metadata:
            metadata = {k: v for k, v in metadata.items() if not k.startswith("injected_")}
            if metadata:
                entry["metadata"] = metadata
            else:
                del entry["metadata"]
        entries.append(entry)
    return json.dumps(entries, indent=1)


def build_system_prompt(variant: str, actions: list[ActionSpec],
                        policies: list[Policy]) -> str:
    core = (PROMPT_DIR / f"planner_{PROMPT_VERSION}.md").read_text()
    prompt = core.replace("{action_catalog}", render_action_catalog(actions))
    if variant == "core":
        return prompt
    if variant == "promptguard":
        section = (PROMPT_DIR / f"planner_promptguard_{PROMPT_VERSION}.md").read_text()
        return prompt + "\n" + section.replace("{policies}", render_policies(policies))
    raise ValueError(f"unknown planner variant: {variant}")


def build_user_input(intent: str, resources: list[Resource]) -> str:
    return f"# Request\n\n{intent}\n\n# Resource inventory\n\n{render_inventory(resources)}\n"


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def parse_plan(payload: str) -> Plan:
    """Flat-nullable schema output -> Plan (nulls dropped from parameters)."""
    data = json.loads(payload)
    actions = []
    for flat in data["actions"]:
        flat = dict(flat)
        name = flat.pop("action_name")
        actions.append(PlannedAction(
            action_name=name,
            parameters={k: v for k, v in flat.items() if v is not None},
        ))
    return Plan(intent_summary=data.get("intent_summary", ""), actions=actions)


# --------------------------------------------------------------------------
# Planners
# --------------------------------------------------------------------------

class Planner(Protocol):
    variant: str
    prompt_sha: str

    def plan_for(self, scenario: Scenario) -> tuple[Plan, PlannerInfo]: ...


class StubPlanner:
    variant = "stub"
    prompt_sha = ""

    def plan_for(self, scenario: Scenario) -> tuple[Plan, PlannerInfo]:
        plan = stub_plan(scenario)
        return plan, PlannerInfo(model="stub", refused=plan.is_refusal)


class OpenAIPlanner:
    def __init__(
        self,
        actions: list[ActionSpec],
        policies: list[Policy],
        *,
        model: str,
        variant: str = "core",
        temperature: float | None = None,
        max_output_tokens: int = 2048,
        timeout_s: float = 120,
        max_retries: int = 4,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.variant = variant
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.system_prompt = build_system_prompt(variant, actions, policies)
        self.prompt_sha = prompt_hash(self.system_prompt)
        self.schema = plan_json_schema({a.action_name for a in actions})
        self.client = client or OpenAI(timeout=timeout_s, max_retries=max_retries)

    def plan_for(self, scenario: Scenario) -> tuple[Plan, PlannerInfo]:
        # The only scenario fields a planner may see: the intent and the
        # starting inventory. Oracle fields never reach the model.
        return self.plan(scenario.natural_language_intent, scenario.initial_state.resources)

    def plan(self, intent: str, resources: list[Resource]) -> tuple[Plan, PlannerInfo]:
        user_input = build_user_input(intent, resources)
        started = time.perf_counter()
        last_error: Exception | None = None
        # Transport-level retries are the SDK's job; this loop re-asks once
        # if the output fails to parse (near-impossible under strict mode).
        for _ in range(2):
            response = self._request(user_input)
            try:
                plan = self._parse_response(response)
            except (ValidationError, ValueError, KeyError) as e:
                last_error = e
                continue
            usage = getattr(response, "usage", None)
            info = PlannerInfo(
                model=getattr(response, "model", self.model),
                response_id=getattr(response, "id", None),
                tokens_in=getattr(usage, "input_tokens", 0) or 0,
                tokens_out=getattr(usage, "output_tokens", 0) or 0,
                latency_ms=(time.perf_counter() - started) * 1000,
                refused=plan.is_refusal,
            )
            return plan, info
        raise PlannerError(f"unparseable plan after retry: {last_error}") from last_error

    def _request(self, user_input: str):
        kwargs: dict[str, Any] = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        return self.client.responses.create(
            model=self.model,
            instructions=self.system_prompt,
            input=user_input,
            max_output_tokens=self.max_output_tokens,
            text={"format": {
                "type": "json_schema",
                "name": "action_plan",
                "schema": self.schema,
                "strict": True,
            }},
            **kwargs,
        )

    @staticmethod
    def _parse_response(response) -> Plan:
        return parse_plan(response.output_text)
