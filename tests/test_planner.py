"""Planner unit tests against a fake OpenAI client (no network, no key)."""

import json
from types import SimpleNamespace

import pytest

from cordon.dataset import load_dataset
from cordon.planner import (
    OpenAIPlanner,
    PlannerError,
    build_system_prompt,
    build_user_input,
    parse_plan,
    plan_json_schema,
    prompt_hash,
    render_inventory,
)


@pytest.fixture(scope="session")
def dataset():
    return load_dataset()


class FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text=self.payloads.pop(0),
            id="resp_test",
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=120, output_tokens=30),
        )


def flat_action(name, **overrides):
    """A schema-shaped action object: every parameter present, unused = null."""
    from cordon.planner import PARAM_FIELDS
    action = {"action_name": name} | {k: None for k in PARAM_FIELDS}
    return action | overrides


def payload(*actions, summary="do the thing"):
    return json.dumps({"intent_summary": summary, "actions": list(actions)})


def make_planner(dataset, variant="core", payloads=()):
    client = FakeClient(payloads)
    planner = OpenAIPlanner(dataset.actions, dataset.policies, model="test-model",
                            variant=variant, client=client)
    return planner, client


# --- schema ------------------------------------------------------------------

def test_schema_is_strict_compatible(dataset):
    schema = plan_json_schema(dataset.action_names())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    item = schema["properties"]["actions"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])  # strict: all required
    assert sorted(dataset.action_names()) == item["properties"]["action_name"]["enum"]


# --- prompts -----------------------------------------------------------------

def test_core_prompt_has_catalog_but_no_policies(dataset):
    prompt = build_system_prompt("core", dataset.actions, dataset.policies)
    assert "create_storage_bucket" in prompt
    assert "set_bucket_public_access" in prompt
    assert "POL-" not in prompt  # no policy/safety language in the shared core


def test_promptguard_extends_the_same_core(dataset):
    core = build_system_prompt("core", dataset.actions, dataset.policies)
    guarded = build_system_prompt("promptguard", dataset.actions, dataset.policies)
    assert guarded.startswith(core)  # identical core = controlled comparison
    assert "POL-NET-001" in guarded and "refuse" in guarded.lower()
    assert prompt_hash(core) != prompt_hash(guarded)


def test_unknown_variant_rejected(dataset):
    with pytest.raises(ValueError):
        build_system_prompt("mystery", dataset.actions, dataset.policies)


# --- inventory ---------------------------------------------------------------

def test_inventory_strips_injected_metadata(dataset):
    indirect = next(s for s in dataset.scenarios if s.injection_type == "indirect")
    rendered = render_inventory(indirect.initial_state.resources)
    assert "injected_port" not in rendered and "injected_cidr" not in rendered
    assert "ADMIN NOTE" in rendered  # the note itself is the injection channel


def test_empty_inventory_placeholder():
    assert render_inventory([]) == "(no resources exist yet)"


def test_planner_never_sees_oracle_fields(dataset):
    s = next(s for s in dataset.scenarios if s.scenario_id == "S061")
    user_input = build_user_input(s.natural_language_intent, s.initial_state.resources)
    assert s.natural_language_intent in user_input
    for oracle_marker in ("expected_decision", "block", "policy_refs", "safety_label"):
        assert oracle_marker not in user_input


# --- request / parse ---------------------------------------------------------

def test_plan_request_and_parse(dataset):
    planner, client = make_planner(dataset, payloads=[payload(
        flat_action("create_storage_bucket", bucket_name="staging-assets-bucket",
                    environment="staging", public_access=False),
        flat_action("enable_bucket_encryption", bucket_name="staging-assets-bucket",
                    encryption_type="AES256"),
    )])
    s = next(s for s in dataset.scenarios if s.scenario_id == "S001")
    plan, info = planner.plan_for(s)

    assert [a.action_name for a in plan.actions] == [
        "create_storage_bucket", "enable_bucket_encryption"]
    assert plan.actions[0].parameters == {
        "bucket_name": "staging-assets-bucket", "environment": "staging",
        "public_access": False}  # nulls dropped
    assert not info.refused
    assert info.tokens_in == 120 and info.response_id == "resp_test"

    call = client.calls[0]
    assert call["instructions"] == planner.system_prompt
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["schema"] == planner.schema
    assert s.natural_language_intent in call["input"]
    assert "temperature" not in call  # null temperature = model default


def test_refusal_is_empty_plan(dataset):
    planner, _ = make_planner(dataset, variant="promptguard",
                              payloads=[payload(summary="violates POL-STO-001")])
    s = next(s for s in dataset.scenarios if s.scenario_id == "S061")
    plan, info = planner.plan_for(s)
    assert plan.is_refusal and info.refused


def test_malformed_output_retries_once_then_succeeds(dataset):
    good = payload(flat_action("inspect_resource_state", resource_type="storage_bucket",
                               resource_id="staging-assets-bucket"))
    planner, client = make_planner(dataset, payloads=["{not json", good])
    plan, _ = planner.plan(intent="inspect the bucket", resources=[])
    assert len(client.calls) == 2
    assert plan.actions[0].action_name == "inspect_resource_state"


def test_persistent_malformed_output_raises(dataset):
    planner, _ = make_planner(dataset, payloads=["{not json", "also not json"])
    with pytest.raises(PlannerError):
        planner.plan(intent="anything", resources=[])


def test_oversized_plan_rejected(dataset):
    too_many = payload(*[
        flat_action("enable_bucket_versioning", bucket_name=f"bucket-{i}")
        for i in range(6)
    ])
    planner, _ = make_planner(dataset, payloads=[too_many, too_many])
    with pytest.raises(PlannerError):
        planner.plan(intent="version all the buckets", resources=[])


def test_parse_plan_rejects_unknown_shape():
    with pytest.raises(KeyError):
        parse_plan(json.dumps({"intent_summary": "x"}))  # missing actions
